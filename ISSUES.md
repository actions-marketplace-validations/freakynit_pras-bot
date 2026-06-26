### All minor

1. `review_engagement.py` ratio can exceed 1.0 (should `min(1.0, ...)`)
2. Imports from `github_client` in `config_loader.py` for private functions — tighter coupling than needed
3. `Retry-After` only handles seconds format, not HTTP-date format
4. GraphQL `_gql()` method exists but is unused
5. No integration/e2e test for the `run()` pipeline
6. Single 1683-line monolithic test file — should be split
7. No schema validation for the YAML config (a Pydantic model or JSON Schema would catch typos early)
8. LLM signal token estimation is rough (`max_input_tokens * 4`)
