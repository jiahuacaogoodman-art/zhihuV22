# -*- coding: utf-8 -*-
"""
冒烟测试 1：健康检查端点
- /health 必须返回 200，且 status=="ok"
- 无需鉴权（中间件不保护此路径）
"""


def test_health_returns_200(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_health_body(client):
    data = client.get("/health").json()
    assert data.get("status") == "ok"
    assert "message" in data
