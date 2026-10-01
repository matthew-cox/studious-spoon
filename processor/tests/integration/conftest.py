from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shortener_testing.fixtures import PgServer


@pytest.fixture(autouse=True)
def _reset_database(reset_database: None) -> None:
    """Every processor integration test starts from the post-migration state."""


@pytest.fixture
async def processor_engine(migrated: PgServer) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated.url("processor_user"))
    yield engine
    await engine.dispose()
