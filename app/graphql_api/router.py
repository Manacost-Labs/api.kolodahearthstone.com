from __future__ import annotations

from fastapi import Request
from strawberry.fastapi import GraphQLRouter

from .repository import get_graphql_repository
from .schema import GraphQLContext, schema


async def get_context(request: Request) -> GraphQLContext:
    return GraphQLContext(repository=get_graphql_repository(), request=request)


graphql_router = GraphQLRouter(
    schema,
    path="/",
    context_getter=get_context,
    graphql_ide=None,
    allow_queries_via_get=False,
    multipart_uploads_enabled=False,
)

# Strawberry registers a disabled GET handler even when queries-via-GET and
# GraphiQL are off. Replace it with a harmless discovery response so opening
# the API address in a browser is informative without exposing a query IDE.
graphql_router.routes = [
    route
    for route in graphql_router.routes
    if not (
        getattr(route, "path", None) == "/"
        and getattr(route, "methods", None) == {"GET"}
    )
]


@graphql_router.get("/", include_in_schema=False)
async def graphql_information() -> dict[str, str]:
    return {
        "name": "Koloda Hearthstone GraphQL API",
        "version": "v1",
        "status": "ok",
        "endpoint": "https://api.kolodahearthstone.com/v1/",
        "method": "POST",
    }
