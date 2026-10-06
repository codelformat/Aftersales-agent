import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.llm import get_extractor
from app.main import app
from app.schemas import AfterSalesRequest

pytestmark = pytest.mark.anyio


def use_extractor(result):
    def _run(_input):
        if isinstance(result, Exception):
            raise result
        return result

    app.dependency_overrides[get_extractor] = lambda: RunnableLambda(_run)


@pytest.fixture(autouse=True)
def _clear():
    yield
    app.dependency_overrides.clear()


async def test_extract_success(client):
    use_extractor({
        "raw": AIMessage(""),
        "parsed": AfterSalesRequest(order_id="A12345", request_type="换货", expected_solution="更换新耳机"),
        "parsing_error": None,
    })
    r = await client.post("/extract", json={"text": "A12345 耳机坏了，换新的"})
    assert r.status_code == 200
    assert r.json() == {"order_id": "A12345", "request_type": "换货", "expected_solution": "更换新耳机"}


async def test_extract_response_is_utf8_not_escaped(client):
    use_extractor({
        "raw": AIMessage(""),
        "parsed": AfterSalesRequest(order_id=None, request_type="换货", expected_solution="更换"),
        "parsing_error": None,
    })
    r = await client.post("/extract", json={"text": "换个新的"})
    assert "换货".encode() in r.content


async def test_parsing_error_returns_502(client):
    use_extractor({"raw": AIMessage("bad"), "parsed": None, "parsing_error": ValueError("bad")})
    r = await client.post("/extract", json={"text": "耳机坏了"})
    assert r.status_code == 502
    assert r.json()["detail"] == {"code": "extraction_failed", "message": "提取失败，请稍后重试"}


async def test_upstream_exception_returns_502(client):
    use_extractor(RuntimeError("secret upstream detail"))
    r = await client.post("/extract", json={"text": "耳机坏了"})
    assert r.status_code == 502
    assert "secret" not in r.text


async def test_blank_text_rejected(client):
    use_extractor(RuntimeError("must not be called"))
    r = await client.post("/extract", json={"text": "  "})
    assert r.status_code == 422
