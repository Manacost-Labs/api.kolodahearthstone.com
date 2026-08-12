from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from graphql import DocumentNode, GraphQLError, OperationDefinitionNode, parse
from graphql.language import (
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    InlineFragmentNode,
    IntValueNode,
    SelectionSetNode,
    VariableNode,
)
from starlette.datastructures import MutableHeaders

from .. import config
from ..redis_cache import TieredCache, get_tiered_cache

GRAPHQL_PATHS = frozenset({"/v1", "/v1/", "/v1/graphql", "/v1/graphql/"})
LIST_FIELDS = frozenset(
    {
        "cards",
        "battlegroundHeroes",
        "statistics",
        "archetypes",
        "battlegroundMinions",
        "sources",
        "datasets",
        "collections",
        "records",
        "search",
        "statisticHistory",
    }
)
DEFAULT_LIST_LIMITS: Mapping[str, int] = {
    "cards": 50,
    "battlegroundHeroes": 50,
    "statistics": 50,
    "archetypes": 50,
    "battlegroundMinions": 50,
    "sources": 100,
    "datasets": 100,
    "collections": 100,
    "records": 50,
    "search": 30,
    "statisticHistory": 50,
}

Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class GraphQLRequestError(ValueError):
    def __init__(self, message: str, *, code: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def _error_body(message: str, code: str) -> bytes:
    return json.dumps(
        {"errors": [{"message": message, "extensions": {"code": code}}]},
        separators=(",", ":"),
    ).encode()


async def _send_json_error(send: Send, error: GraphQLRequestError) -> None:
    body = _error_body(error.message, error.code)
    await send(
        {
            "type": "http.response.start",
            "status": error.status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _read_body(receive: Receive, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            raise GraphQLRequestError(
                "The client disconnected before the request was complete",
                code="CLIENT_DISCONNECTED",
                status=400,
            )
        chunk = bytes(message.get("body") or b"")
        size += len(chunk)
        if size > max_bytes:
            raise GraphQLRequestError(
                "GraphQL request body is too large",
                code="REQUEST_TOO_LARGE",
                status=413,
            )
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def _single_body_receive(body: bytes) -> Receive:
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def _json_payload(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GraphQLRequestError(
            "Request body must be a JSON object",
            code="INVALID_REQUEST",
        ) from None
    if not isinstance(payload, dict):
        raise GraphQLRequestError(
            "Request body must be a JSON object",
            code="INVALID_REQUEST",
        )
    return payload


async def _resolve_persisted_query(
    payload: dict[str, Any], cache: TieredCache
) -> tuple[dict[str, Any], bool]:
    extensions = payload.get("extensions")
    persisted = (
        extensions.get("persistedQuery") if isinstance(extensions, dict) else None
    )
    if persisted is None:
        return payload, False
    if not isinstance(persisted, dict) or persisted.get("version") != 1:
        raise GraphQLRequestError(
            "Only persisted query version 1 is supported",
            code="PERSISTED_QUERY_NOT_SUPPORTED",
        )
    digest = persisted.get("sha256Hash")
    if not isinstance(digest, str) or len(digest) != 64:
        raise GraphQLRequestError(
            "Persisted query hash must be a SHA-256 digest",
            code="INVALID_PERSISTED_QUERY",
        )
    try:
        int(digest, 16)
    except ValueError:
        raise GraphQLRequestError(
            "Persisted query hash must be a SHA-256 digest",
            code="INVALID_PERSISTED_QUERY",
        ) from None

    query = payload.get("query")
    cache_key = f"apq:{digest.lower()}"
    if query is None:
        stored, _layer = await cache.get(cache_key)
        if stored is None:
            raise GraphQLRequestError(
                "PersistedQueryNotFound",
                code="PERSISTED_QUERY_NOT_FOUND",
                status=200,
            )
        resolved = dict(payload)
        resolved["query"] = stored.decode("utf-8")
        return resolved, True
    if not isinstance(query, str):
        raise GraphQLRequestError("query must be a string", code="INVALID_REQUEST")
    actual = hashlib.sha256(query.encode()).hexdigest()
    if actual != digest.lower():
        raise GraphQLRequestError(
            "Persisted query hash does not match query text",
            code="PERSISTED_QUERY_HASH_MISMATCH",
        )
    await cache.set(
        cache_key,
        query.encode(),
        ttl_seconds=config.graphql_persisted_query_ttl_seconds(),
    )
    return payload, True


def _argument_limit(
    field: FieldNode,
    variables: Mapping[str, Any],
) -> int:
    default = DEFAULT_LIST_LIMITS.get(field.name.value, 50)
    argument = next(
        (item for item in field.arguments or () if item.name.value == "limit"),
        None,
    )
    if argument is None:
        return default
    value: object
    if isinstance(argument.value, IntValueNode):
        value = argument.value.value
    elif isinstance(argument.value, VariableNode):
        value = variables.get(argument.value.name.value, default)
    else:
        return 200
    try:
        return max(1, min(10_000, int(value)))
    except (TypeError, ValueError):
        return 200


def _selection_cost(
    selection_set: SelectionSetNode | None,
    *,
    variables: Mapping[str, Any],
    fragments: Mapping[str, FragmentDefinitionNode],
    fragment_stack: frozenset[str] = frozenset(),
) -> int:
    if selection_set is None:
        return 0
    total = 0
    for selection in selection_set.selections:
        if isinstance(selection, FieldNode):
            child = _selection_cost(
                selection.selection_set,
                variables=variables,
                fragments=fragments,
                fragment_stack=fragment_stack,
            )
            multiplier = (
                _argument_limit(selection, variables)
                if selection.name.value in LIST_FIELDS
                else 1
            )
            total += 1 + multiplier * child
        elif isinstance(selection, InlineFragmentNode):
            total += _selection_cost(
                selection.selection_set,
                variables=variables,
                fragments=fragments,
                fragment_stack=fragment_stack,
            )
        elif isinstance(selection, FragmentSpreadNode):
            name = selection.name.value
            if name in fragment_stack:
                raise GraphQLRequestError(
                    "GraphQL fragments must not form a cycle",
                    code="INVALID_REQUEST",
                )
            fragment = fragments.get(name)
            if fragment is not None:
                total += _selection_cost(
                    fragment.selection_set,
                    variables=variables,
                    fragments=fragments,
                    fragment_stack=fragment_stack | {name},
                )
    return total


def query_complexity(
    document: DocumentNode,
    *,
    variables: Mapping[str, Any],
    operation_name: str | None,
) -> int:
    fragments = {
        definition.name.value: definition
        for definition in document.definitions
        if isinstance(definition, FragmentDefinitionNode)
    }
    operations = [
        definition
        for definition in document.definitions
        if isinstance(definition, OperationDefinitionNode)
        and (
            operation_name is None
            or (definition.name is not None and definition.name.value == operation_name)
        )
    ]
    if not operations:
        return 0
    return max(
        _selection_cost(
            operation.selection_set,
            variables=variables,
            fragments=fragments,
        )
        for operation in operations
    )


def _parse_and_guard(payload: Mapping[str, Any]) -> tuple[DocumentNode, int]:
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        raise GraphQLRequestError("query is required", code="INVALID_REQUEST")
    variables = payload.get("variables") or {}
    if not isinstance(variables, dict):
        raise GraphQLRequestError("variables must be an object", code="INVALID_REQUEST")
    operation_name = payload.get("operationName")
    if operation_name is not None and not isinstance(operation_name, str):
        raise GraphQLRequestError(
            "operationName must be a string",
            code="INVALID_REQUEST",
        )
    try:
        document = parse(query)
    except GraphQLError as error:
        raise GraphQLRequestError(
            error.message,
            code="GRAPHQL_PARSE_FAILED",
            status=200,
        ) from None
    complexity = query_complexity(
        document,
        variables=variables,
        operation_name=operation_name,
    )
    maximum = config.graphql_max_complexity()
    if complexity > maximum:
        raise GraphQLRequestError(
            f"GraphQL query complexity {complexity} exceeds the maximum {maximum}",
            code="QUERY_TOO_COMPLEX",
            status=200,
        )
    return document, complexity


def _is_query(document: DocumentNode, operation_name: str | None) -> bool:
    operations = [
        definition
        for definition in document.definitions
        if isinstance(definition, OperationDefinitionNode)
        and (
            operation_name is None
            or (definition.name is not None and definition.name.value == operation_name)
        )
    ]
    return len(operations) == 1 and operations[0].operation.value == "query"


def _cache_key(payload: Mapping[str, Any]) -> str:
    normalized = {
        "query": payload.get("query"),
        "variables": payload.get("variables") or {},
        "operationName": payload.get("operationName"),
    }
    encoded = json.dumps(
        normalized,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return f"graphql:response:{hashlib.sha256(encoded).hexdigest()}"


def _has_credentials(scope: Mapping[str, Any]) -> bool:
    return any(
        name.lower() in {b"authorization", b"x-api-key"}
        for name, _value in scope.get("headers", [])
    )


async def _send_cached(send: Send, body: bytes, layer: str, complexity: int) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"private, max-age=0"),
                (b"vary", b"Authorization, X-API-Key"),
                (b"x-koloda-cache", layer.upper().encode()),
                (b"x-graphql-complexity", str(complexity).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class GraphQLGovernanceMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self, scope: dict[str, Any], receive: Receive, send: Send
    ) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in GRAPHQL_PATHS
        ):
            await self.app(scope, receive, send)
            return

        cache = get_tiered_cache()
        try:
            request_body = await _read_body(receive, config.graphql_max_request_bytes())
            payload = _json_payload(request_body)
            payload, _persisted = await _resolve_persisted_query(payload, cache)
            document, complexity = _parse_and_guard(payload)
        except GraphQLRequestError as error:
            await _send_json_error(send, error)
            return

        operation_name = payload.get("operationName")
        cacheable = (
            config.graphql_cache_ttl_seconds() > 0
            and _is_query(document, operation_name)
            and not _has_credentials(scope)
        )
        response_cache_key = _cache_key(payload)
        if cacheable:
            cached, layer = await cache.get(response_cache_key)
            if cached is not None:
                await _send_cached(send, cached, layer, complexity)
                return

        encoded_payload = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        forwarded_scope = dict(scope)
        forwarded_headers = [
            (name, value)
            for name, value in scope.get("headers", [])
            if name.lower() != b"content-length"
        ]
        forwarded_headers.append(
            (b"content-length", str(len(encoded_payload)).encode())
        )
        forwarded_scope["headers"] = forwarded_headers

        messages: list[dict[str, Any]] = []

        async def capture(message: dict[str, Any]) -> None:
            messages.append(dict(message))

        try:
            async with asyncio.timeout(config.graphql_timeout_seconds()):
                await self.app(
                    forwarded_scope,
                    _single_body_receive(encoded_payload),
                    capture,
                )
        except TimeoutError:
            await _send_json_error(
                send,
                GraphQLRequestError(
                    "GraphQL request exceeded its execution deadline",
                    code="QUERY_TIMEOUT",
                    status=504,
                ),
            )
            return

        body = b"".join(
            bytes(message.get("body") or b"")
            for message in messages
            if message.get("type") == "http.response.body"
        )
        if len(body) > config.graphql_max_response_bytes():
            await _send_json_error(
                send,
                GraphQLRequestError(
                    "GraphQL response exceeds the configured size limit",
                    code="RESPONSE_TOO_LARGE",
                    status=413,
                ),
            )
            return

        start = next(
            (
                message
                for message in messages
                if message.get("type") == "http.response.start"
            ),
            None,
        )
        status = int(start.get("status") or 500) if start else 500
        response_has_errors = True
        if status == 200:
            try:
                response_has_errors = "errors" in json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                response_has_errors = True
        if cacheable and status == 200 and not response_has_errors:
            await cache.set(
                response_cache_key,
                body,
                ttl_seconds=config.graphql_cache_ttl_seconds(),
            )

        for message in messages:
            if message.get("type") == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Koloda-Cache"] = "MISS" if cacheable else "BYPASS"
                headers["X-GraphQL-Complexity"] = str(complexity)
                headers["Vary"] = "Authorization, X-API-Key"
            await send(message)
