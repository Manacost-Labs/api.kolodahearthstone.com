"""Public GraphQL API backed by the central PostgreSQL data hub."""

from .router import graphql_router

__all__ = ["graphql_router"]
