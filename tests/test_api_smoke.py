import requests
import pytest

# 简单实现一个可以调用http接口的get接口，验证下返回
BASE_URL = "https://jsonplaceholder.typicode.com"


@pytest.mark.smoke
def test_get_post_by_id_returns_post():
    response = requests.get(f"{BASE_URL}/posts/1", timeout=5)

    assert response.status_code == 200

    post = response.json()
    assert post["id"] == 1
    assert {"userId", "title", "body"} <= post.keys()
