from rubygemdb.services.classifier import GemClassifier

# Mock dependencies
class DummyRubyGems:
    def fetch_gem_info(self, name):
        return None

class DummyLLM:
    def build_prompt(self, name, info, deps):
        return ""
    def call_llm(self, prompt):
        return {"primary": "framework_integration", "confidence": 0.7}


def test_sub_category_sidekiq_detection():
    classifier = GemClassifier(DummyRubyGems(), DummyLLM())
    info = {"dependencies": {"runtime": [{"name": "sidekiq"}]}}
    classification, signals, deps, sub_cats = classifier.heuristic_classify("testgem", info)
    assert "sidekiq" in sub_cats
    assert "background_jobs" in sub_cats


def test_sub_category_multiple_patterns():
    classifier = GemClassifier(DummyRubyGems(), DummyLLM())
    info = {"dependencies": {"runtime": [{"name": "rails"}, {"name": "redis"}]}}
    classification, signals, deps, sub_cats = classifier.heuristic_classify("testgem", info)
    assert "rails" in sub_cats
    assert "redis" in sub_cats


def test_sub_category_no_dependencies():
    classifier = GemClassifier(DummyRubyGems(), DummyLLM())
    info = {"dependencies": {"runtime": []}}
    classification, signals, deps, sub_cats = classifier.heuristic_classify("testgem", info)
    assert len(sub_cats) == 0


def test_sub_category_auth_patterns():
    classifier = GemClassifier(DummyRubyGems(), DummyLLM())
    info = {"dependencies": {"runtime": [{"name": "jwt"}, {"name": "oauth2"}]}}
    classification, signals, deps, sub_cats = classifier.heuristic_classify("testgem", info)
    assert "auth" in sub_cats


def test_sub_category_monitoring_patterns():
    classifier = GemClassifier(DummyRubyGems(), DummyLLM())
    info = {"dependencies": {"runtime": [{"name": "sentry-ruby"}]}}
    classification, signals, deps, sub_cats = classifier.heuristic_classify("testgem", info)
    assert "monitoring" in sub_cats


def test_sub_category_background_job_alternatives():
    classifier = GemClassifier(DummyRubyGems(), DummyLLM())
    info = {"dependencies": {"runtime": [{"name": "resque"}]}}
    classification, signals, deps, sub_cats = classifier.heuristic_classify("testgem", info)
    assert "background_jobs" in sub_cats


def test_sub_category_database_patterns():
    classifier = GemClassifier(DummyRubyGems(), DummyLLM())
    info = {"dependencies": {"runtime": [{"name": "pg"}, {"name": "mysql2"}]}}
    classification, signals, deps, sub_cats = classifier.heuristic_classify("testgem", info)
    assert "database" in sub_cats


def test_sub_category_cloud_patterns():
    classifier = GemClassifier(DummyRubyGems(), DummyLLM())
    info = {"dependencies": {"runtime": [{"name": "aws-sdk-s3"}]}}
    classification, signals, deps, sub_cats = classifier.heuristic_classify("testgem", info)
    assert "cloud" in sub_cats


def test_sub_category_messaging():
    classifier = GemClassifier(DummyRubyGems(), DummyLLM())
    info = {"dependencies": {"runtime": [{"name": "bunny"}]}}
    classification, signals, deps, sub_cats = classifier.heuristic_classify("testgem", info)
    assert "messaging" in sub_cats


def test_sub_category_case_insensitive():
    classifier = GemClassifier(DummyRubyGems(), DummyLLM())
    info = {"dependencies": {"runtime": [{"name": "Sidekiq"}]}}
    classification, signals, deps, sub_cats = classifier.heuristic_classify("testgem", info)
    assert "sidekiq" in sub_cats
    assert "background_jobs" in sub_cats


def test_sub_category_combined_complex():
    classifier = GemClassifier(DummyRubyGems(), DummyLLM())
    info = {"dependencies": {"runtime": [
        {"name": "rails"},
        {"name": "sidekiq"},
        {"name": "pg"},
        {"name": "redis"},
        {"name": "sentry-ruby"}
    ]}}
    classification, signals, deps, sub_cats = classifier.heuristic_classify("testgem", info)
    expected = {"rails", "sidekiq", "background_jobs", "database", "redis", "monitoring"}
    assert expected.issubset(set(sub_cats))
