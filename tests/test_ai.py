"""AI 계층 순수 로직 테스트 — 감성 라벨, mock 임베딩, 클러스터링."""

import numpy as np

from src.ai.cluster import _greedy_cluster
from src.ai.embeddings import _mock_vector
from src.ai.sentiment import mock_label, normalize_label, to_korean


class TestSentiment:
    def test_normalize_english(self):
        assert normalize_label("positive") == "positive"

    def test_normalize_korean_aliases(self):
        assert normalize_label("긍정") == "positive"
        assert normalize_label("부정적") == "negative"

    def test_normalize_unknown_defaults_neutral(self):
        assert normalize_label("이상한값") == "neutral"
        assert normalize_label(None) == "neutral"

    def test_mock_label_by_hints(self):
        assert mock_label("수출 성장 흑자 확대") == "positive"
        assert mock_label("적자 위기 논란 하락") == "negative"
        assert mock_label("평범한 기사") == "neutral"

    def test_to_korean(self):
        assert to_korean("positive") == "긍정"
        assert to_korean("unknown") == "미분류"


class TestMockEmbedding:
    def test_deterministic(self):
        assert _mock_vector("삼성전자 반도체") == _mock_vector("삼성전자 반도체")

    def test_normalized_unit_vector(self):
        v = np.array(_mock_vector("어떤 텍스트입니다"))
        assert np.isclose(np.linalg.norm(v), 1.0)

    def test_similar_texts_more_similar_than_unrelated(self):
        a = np.array(_mock_vector("삼성전자 반도체 실적 발표"))
        b = np.array(_mock_vector("삼성전자 반도체 실적 호조"))
        c = np.array(_mock_vector("날씨 폭염 주의보 발효"))
        assert float(a @ b) > float(a @ c)

    def test_empty_text(self):
        assert len(_mock_vector("")) == 256


class TestGreedyCluster:
    def test_identical_vectors_group_together(self):
        v = np.array([1.0, 0.0])
        vectors = np.array([v, v, v])
        groups = _greedy_cluster(vectors, threshold=0.9)
        assert len(groups) == 1 and sorted(groups[0]) == [0, 1, 2]

    def test_orthogonal_vectors_separate(self):
        vectors = np.array([[1.0, 0.0], [0.0, 1.0]])
        groups = _greedy_cluster(vectors, threshold=0.5)
        assert len(groups) == 2

    def test_threshold_controls_grouping(self):
        # 코사인 ~0.85 인 두 벡터
        vectors = np.array([[1.0, 0.0], [1.0, 0.6]])
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        assert len(_greedy_cluster(vectors, threshold=0.95)) == 2  # 엄격 → 분리
        assert len(_greedy_cluster(vectors, threshold=0.5)) == 1   # 느슨 → 묶임
