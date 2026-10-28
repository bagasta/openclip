import unittest
from fastapi.testclient import TestClient
from openclip.app import app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_rejects_bad_duration_before_accepting_source(self):
        response = self.client.post('/api/projects', data={'url': 'https://example.com/video.mp4', 'min_duration': '45', 'max_duration': '20'})
        self.assertEqual(response.status_code, 422)

    def test_cross_origin_mutation_is_rejected(self):
        response = self.client.post('/api/projects', headers={'Origin': 'https://example.com'})
        self.assertEqual(response.status_code, 403)

    def test_missing_project_is_404_and_does_not_resolve_paths(self):
        self.assertEqual(self.client.get('/api/projects/not-a-project').status_code, 404)
        self.assertEqual(self.client.get('/api/projects/not-a-project/clips/clip1/video').status_code, 404)


if __name__ == '__main__':
    unittest.main()
