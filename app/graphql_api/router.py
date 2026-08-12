from __future__ import annotations

from fastapi import Request
from strawberry.fastapi import GraphQLRouter

from .repository import get_graphql_repository
from .schema import GraphQLContext, schema


async def get_context(request: Request) -> GraphQLContext:
    return GraphQLContext(repository=get_graphql_repository(), request=request)


def build_graphql_router(
    *,
    path: str,
    public_endpoint: str,
    deprecated: bool = False,
) -> GraphQLRouter:
    router = GraphQLRouter(
        schema,
        path=path,
        context_getter=get_context,
        graphql_ide=None,
        allow_queries_via_get=False,
        multipart_uploads_enabled=False,
        deprecated=deprecated,
    )

    # Strawberry registers a disabled GET handler even when queries-via-GET and
    # GraphiQL are off. Replace it with a harmless discovery response so opening
    # the API address in a browser is informative without exposing a query IDE.
    router.routes = [
        route
        for route in router.routes
        if not (
            getattr(route, "path", None) == path
            and getattr(route, "methods", None) == {"GET"}
        )
    ]

    async def graphql_information() -> dict[str, str]:
        return {
            "name": "Koloda Hearthstone GraphQL API",
            "version": "v1",
            "status": "ok",
            "endpoint": public_endpoint,
            "method": "POST",
        }

    router.add_api_route(
        path,
        graphql_information,
        methods=["GET"],
        include_in_schema=False,
    )
    return router


graphql_router = build_graphql_router(
    path="/",
    public_endpoint="https://api.kolodahearthstone.com/v1/",
    deprecated=True,
)
canonical_graphql_router = build_graphql_router(
    path="",
    public_endpoint="https://api.kolodahearthstone.com/v1/graphql",
)
