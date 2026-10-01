import pytest

from shortener_processor.link_resolver import PostgresLinkResolver

pytestmark = pytest.mark.integration


class FakeMonotonic:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_resolves_known_codes_and_omits_unknown(processor_engine, insert_link):
    link = insert_link(code="aZ3kQ9x")
    resolved = await PostgresLinkResolver(processor_engine).resolve({"aZ3kQ9x", "nope123"})
    assert resolved == {"aZ3kQ9x": link}


async def test_codes_are_case_sensitive(processor_engine, insert_link):
    insert_link(code="aZ3kQ9x")
    assert await PostgresLinkResolver(processor_engine).resolve({"AZ3KQ9X"}) == {}


async def test_hits_are_cached_until_ttl_expires(processor_engine, insert_link, migrated):
    link = insert_link(code="cache01")
    clock = FakeMonotonic()
    resolver = PostgresLinkResolver(processor_engine, ttl_seconds=60, monotonic=clock)
    assert await resolver.resolve({"cache01"}) == {"cache01": link}
    with migrated.connect("migrator") as conn:
        conn.execute("DELETE FROM public.links WHERE id = %s", (link,))
    assert await resolver.resolve({"cache01"}) == {"cache01": link}  # served from cache
    clock.now += 61
    assert await resolver.resolve({"cache01"}) == {}


async def test_misses_are_not_cached(processor_engine, insert_link):
    resolver = PostgresLinkResolver(processor_engine)
    assert await resolver.resolve({"later01"}) == {}
    link = insert_link(code="later01")
    assert await resolver.resolve({"later01"}) == {"later01": link}


async def test_empty_input_does_not_query(processor_engine):
    assert await PostgresLinkResolver(processor_engine).resolve(set()) == {}
