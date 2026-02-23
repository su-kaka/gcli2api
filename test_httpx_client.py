from src.httpx_client import _encode_json_body_safely


def test_encode_json_body_safely_keeps_valid_unicode():
    payload = {"x": "😀"}
    encoded = _encode_json_body_safely(payload)
    assert encoded.decode("utf-8") == '{"x":"😀"}'


def test_encode_json_body_safely_falls_back_on_lone_surrogate():
    payload = {"x": "\ud83d"}
    encoded = _encode_json_body_safely(payload)
    assert encoded.decode("utf-8") == '{"x":"\\ud83d"}'
