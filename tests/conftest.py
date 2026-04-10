"""pytest 공통 설정."""

import os

# 테스트용 API 키 (실제 호출은 모두 mock 처리)
os.environ.setdefault("DART_API_KEY", "test-dart-key")
os.environ.setdefault("FISIS_API_KEY", "test-fisis-key")
