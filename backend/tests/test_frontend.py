def test_frontend_and_assets_are_served_alongside_api(client):
    response = client.get('/')
    assert response.status_code == 200
    assert 'CashFlow AI' in response.text
    assert 'lang="ru"' in response.text
    for asset in ('app.js', 'api.js', 'presentation.js', 'styles.css'):
        response = client.get(f'/assets/{asset}')
        assert response.status_code == 200
    assert client.get('/api/transactions').json() == []
    assert client.get('/docs').status_code == 200


def test_asset_mount_does_not_replace_missing_api_errors(client):
    assert client.get('/api/missing-module').status_code == 404
    assert client.get('/assets/missing.js').status_code == 404
