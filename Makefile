.DEFAULT_GOAL := help
.PHONY: help test mutate check convert build serve clean checksums checksums-update

# Every target here runs the same way locally and in CI. The workflow calls
# these and nothing else, so a green pipeline is reproducible on a laptop.
#
# Both toolchains run through `uv run --with`, which means there is no install
# step to keep in sync and no ambient version to disagree about.
#
# Everything is pinned, because both outputs are published artifacts. A floating
# site generator can change the built page for a commit that changed nothing. A
# floating validator is worse: the invalid/ fixtures assert *where* each error
# is reported, so a change in how jsonschema builds its error paths would turn
# the normative suite red without anyone touching the spec.
PY := uv run --python 3.12 --with jsonschema==4.26.0 python
ZENSICAL := uv run --with zensical==0.0.31 zensical
SCHEMA := docs/schema/v0.3.json

help: ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-17s\033[0m %s\n", $$1, $$2}'

test: ## Run the normative fixture suite
	$(PY) tests/run.py

mutate: ## Prove the fixture suite can fail (breaks each rule in turn)
	$(PY) tests/mutate.py

check: ## Validate a schema document: make check FILE=path/to/schema.json
	@test -n "$(FILE)" || { echo "usage: make check FILE=<schema.json> [STRICT=1]"; exit 2; }
	$(PY) tools/clispec_validate.py $(if $(STRICT),--strict,) $(FILE)

convert: ## Convert a v0.2 document: make convert FILE=old.json [OUT=new.json]
	@test -n "$(FILE)" || { echo "usage: make convert FILE=<v0.2.json> [OUT=<v0.3.json>]"; exit 2; }
	$(PY) tools/clispec_convert.py $(FILE) $(if $(OUT),-o $(OUT),)

checksums: ## Verify no frozen schema has changed since it was published
	$(PY) tools/freeze.py

checksums-update: ## Re-record checksums (refuses to move a frozen one)
	$(PY) tools/freeze.py --update

build: ## Build the site
	$(ZENSICAL) build --clean
	@# A frozen version's prose is a published artifact, not only a page. The
	@# checksum covers the source, so the source is served beside the rendering.
	cp docs/spec/*.md site/spec/
	$(PY) tools/freeze.py --site

serve: ## Serve the site locally
	$(ZENSICAL) serve

clean: ## Remove build output and caches
	rm -rf site
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
