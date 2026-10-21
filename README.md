# blue-data-planner

Operator-agnostic data planners for the Blue **Data Intelligence Layer (DIL)**.

A planner turns a plain-language request into a declarative plan (a DAG of operator calls), runs
the plan, and repairs it when a call fails. The planner depends on `blue-data-ops` for the Blue
operators and their registry, and on `blue-data` for data sources and the data registry.

→ **[Full documentation](docs/README.md)** · **[Quick start](docs/quickstart.md)** · **[Installation](docs/installation.md)** · **[Examples](examples/README.md)** · **[CLI](docs/cli.md)** · **[MCP](docs/mcp.md)**

## What it does

- **Plan** a request as a DAG of logical operators. Each `SCAN` names the catalog entity it reads,
  and the planner searches a `blue-data` registry when the request does not name its tables.
- **Push down** as much of the plan as each operator can take. A compound operator such as
  `blue.nl2sql` runs several nodes in one call, while other operators take one node each.
- **Run** each fragment, the group of nodes one operator call covers, with parameters a model
  writes. The model reviews the returned rows before the planner accepts them.
- **Repair** a failure in three ordered steps, each rewriting more than the one before: the
  parameters of one call, then the assignment of nodes to operators, then the plan itself.
- **Combine** the deterministic `builtin` operators with the Blue operators from `blue-data-ops`,
  which enter through their own adapter.
- **Inspect** a finished run. One `RunResult` holds the plans, the per-fragment results, the failure
  reasons, and every model exchange as structured data.

See [docs/features.md](docs/features.md) for the full list with snippets, and
[examples/](examples/README.md) for runnable scripts with captured output.

## Repository layout

```text
blue-data-planner/
├── lib/                    # Python library (blue-data-planner)
│   ├── src/blue_data_planner/
│   ├── scripts/            # install and build scripts, demo registry scripts
│   └── tests/              # unit tests and the TAG-Bench harness
├── platform/
│   ├── planner-cli/        # Command-line interface (blue-planner)
│   └── planner-mcp/        # MCP server (blue-data-planner-mcp)
├── docs/                   # User-facing documentation (start at docs/README.md)
├── examples/               # Runnable examples over the demo registry
├── design/                 # Numbered design documents and the decision log
├── data/                   # Demo and benchmark data written by the scripts (git-ignored)
└── example.envrc           # Environment template: copy to .envrc and fill in
```

## Requirements

- Python 3.11 or later
- [`blue-data`](../blue-data) and [`blue-data-ops`](../blue-data-ops) checked out beside this
  repository. The install scripts install both from there.
- An OpenAI API key for the default model. Every planning stage calls a model.

```bash
export OPENAI_API_KEY=sk-...
```

The CLI and the MCP server read their configuration from the environment. `example.envrc` is a
template: copy it to `.envrc` and load it with `direnv allow` or `source .envrc`.
[docs/installation.md](docs/installation.md) lists every variable and its default.

## Quick start

From the repository root, with the two sibling checkouts in place:

```bash
# Install the library and the CLI
python -m venv .venv && source .venv/bin/activate
bash platform/planner-cli/scripts/install.sh

# Build the Northwind demo registry (no model calls)
python lib/scripts/demo/fetch_northwind.py
python lib/scripts/demo/register_northwind.py

# Point the planner at it
export OPENAI_API_KEY=sk-...                      # a real key, not this placeholder
export BLUE_DATA_REGISTRY_PATH=$PWD/data/demo/registry.json
export BLUE_DATA_PLANNER_MODEL=openai/gpt-6-luna
export BLUE_DATA_PLANNER_OPERATORS=builtin        # the built-in operators only

# Ask a question
blue-planner data query "Which three countries do the most customers come from?"
```

[docs/quickstart.md](docs/quickstart.md) walks through the same steps with the expected output,
shows how to read back what the run did, and runs the same request from Python.

## Packages

| Package | Description |
| --- | --- |
| [lib/](lib/README.md) | `blue-data-planner`: the planner library |
| [platform/planner-cli/](platform/planner-cli/README.md) | `blue-data-planner-cli`: the `blue-planner` command |
| [platform/planner-mcp/](platform/planner-mcp/README.md) | `blue-data-planner-mcp`: the MCP server of the same name, whose one tool, `query_data`, runs the planner |

## Code of conduct

Participation in this project is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

## Disclosures

Megagon Labs, Inc. is the original source and copyright holder of
[blue-data-planner](https://github.com/megagon-internal/blue-data-planner), distributed under the
[repository license](LICENSE).

This software may include, incorporate, or access open source software (OSS) components,
datasets and other third party components, including those identified below. The license terms
respectively governing the datasets and third-party components continue to govern those
portions, and you agree to those license terms may limit any distribution, use, and copying.
You may use any OSS components under the terms of their respective licenses, which may
include BSD 3, Apache 2.0, and other licenses. In the event of conflicts between Megagon Labs,
Inc. (“Megagon”) license conditions and the OSS license conditions, the applicable OSS
conditions governing the corresponding OSS components shall prevail.
You agree not to, and are not permitted to, distribute actual datasets used with the OSS
components listed below. You agree and are limited to distribute only links to datasets from
known sources by listing them in the datasets overview table below. You agree that any right to
modify datasets originating from parties other than Megagon are governed by the respective
third party’s license conditions.
You agree that Megagon grants no license as to any of its intellectual property and patent rights.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS (INCLUDING
MEGAGON) “AS IS” AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE. You agree to cease using,
incorporating, and distributing any part of the provided materials if you do not agree with the
terms or the lack of any warranty herein.
While Megagon makes commercially reasonable efforts to ensure that citations in this
document are complete and accurate, errors may occur. If you see any error or omission, please
help us improve this document by sending information to contact_oss@megagon.ai.


### Datasets

The demo and benchmark scripts use the datasets listed below. They copy or download inputs into
the git-ignored `data/` directory; the dataset files are not distributed with this repository or
its Python packages. The benchmark preprocessing corrects TAG-Bench questions and answers and
assigns development and test splits. The BIRD SQLite database files are extracted without changes.

For datasets with portions released under different licenses, refer to the upstream links for
the terms and attribution that apply to each portion.

| ID | Dataset | Modified | Copyright Holder | Upstream Link | License |
| --- | --- | --- | --- | --- | --- |
| 1 | Northwind demo seed and descriptions | No (copied from blue-data) | Microsoft Corporation (Northwind); Megagon Labs, Inc. (Blue demo files) | [Blue demo data](https://github.com/megagon-internal/blue-data/tree/dev/data), [original Northwind samples](https://github.com/microsoft/sql-server-samples/tree/master/samples/databases/northwind-pubs) | See the Northwind license note below |
| 2 | TAG-Bench questions and answers | Yes (corrections and split assignments) | Future Data Systems | [TAG-Bench](https://github.com/TAG-Research/TAG-Bench), [query release](https://github.com/TAG-Research/TAG-Bench/blob/main/tag_queries.csv) | [MIT](https://github.com/TAG-Research/TAG-Bench/blob/main/LICENSE) |
| 3 | BIRD development databases used by TAG-Bench | No | BIRD authors and original database copyright holders | [BIRD](https://bird-bench.github.io/), [dataset documentation](https://github.com/AlibabaResearch/DAMO-ConvAI/tree/main/bird) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) for the BIRD release; see upstream for original database terms |

The Northwind demo copies `northwind_sqlite.sql` and `northwind_descriptions.json` from
`blue-data/data/`. The Blue repository supplies a
[BSD 3-Clause License with an Attribution / NOTICE Requirement](https://github.com/megagon-internal/blue-data/blob/dev/LICENSE)
and does not identify a separate dataset license for those files. Microsoft publishes its
original Northwind samples under the
[MIT License](https://github.com/microsoft/sql-server-samples/blob/master/license.txt).
Refer to both upstream sources for the applicable terms and attribution.

The BIRD databases selected by the TAG-Bench queries are `california_schools`,
`codebase_community`, `debit_card_specializing`, `european_football_2`, and `formula_1`.
TAG-Bench's MIT license does not replace the terms governing these databases.

### Open Source Software (OSS) Components

The table covers direct runtime, optional, development, and build dependencies declared in
[lib/pyproject.toml](lib/pyproject.toml),
[platform/planner-cli/pyproject.toml](platform/planner-cli/pyproject.toml), and
[platform/planner-mcp/pyproject.toml](platform/planner-mcp/pyproject.toml), plus packages imported
directly by the [examples](examples/README.md). The planner packages developed in this repository
are covered by the repository license above.

These components are used without modifying their upstream source. Dependencies brought in by
these components retain their own licenses and notices. For components with portions released
under different licenses, refer to the upstream license files for the terms and attribution
that apply to each portion.

| ID | OSS Component Name | Modified | Copyright Holder | Upstream Link | License |
| --- | --- | --- | --- | --- | --- |
| 1 | anyio | No | Alex Grönholm | [anyio](https://github.com/agronholm/anyio) | [MIT](https://github.com/agronholm/anyio/blob/master/LICENSE) |
| 2 | blue-data | No | Megagon Labs, Inc. | [blue-data](https://github.com/megagon-internal/blue-data) | [BSD 3-Clause with Attribution / NOTICE Requirement](https://github.com/megagon-internal/blue-data/blob/dev/LICENSE) |
| 3 | blue-data-ops | No | Megagon Labs, Inc. | [blue-data-ops](https://github.com/megagon-internal/blue-data-ops) | [BSD 3-Clause with Attribution / NOTICE Requirement](https://github.com/megagon-internal/blue-data-ops/blob/dev/LICENSE) |
| 4 | build | No | Filipe Laíns | [build](https://github.com/pypa/build) | [MIT](https://github.com/pypa/build/blob/main/LICENSE) |
| 5 | click | No | Pallets | [click](https://github.com/pallets/click) | [BSD 3-Clause](https://github.com/pallets/click/blob/main/LICENSE.txt) |
| 6 | diskcache | No | Grant Jenks | [diskcache](https://github.com/grantjenks/python-diskcache) | [Apache 2.0](https://github.com/grantjenks/python-diskcache/blob/master/LICENSE) |
| 7 | jinja2 | No | Pallets | [Jinja](https://github.com/pallets/jinja) | [BSD 3-Clause](https://github.com/pallets/jinja/blob/main/LICENSE.txt) |
| 8 | jsonpath-ng | No | Kenneth Knowles; Tomas Aparicio | [jsonpath-ng](https://pypi.org/project/jsonpath-ng/) | [Apache 2.0](https://pypi.org/project/jsonpath-ng/1.8.0/#copyright-and-license) |
| 9 | langchain | No | LangChain, Inc. | [LangChain](https://github.com/langchain-ai/langchain) | [MIT](https://github.com/langchain-ai/langchain/blob/master/LICENSE) |
| 10 | langchain-core | No | LangChain, Inc. | [LangChain Core](https://github.com/langchain-ai/langchain/tree/master/libs/core) | [MIT](https://github.com/langchain-ai/langchain/blob/master/libs/core/LICENSE) |
| 11 | langchain-mcp-adapters | No | LangChain, Inc. | [LangChain MCP Adapters](https://github.com/langchain-ai/langchain-mcp-adapters) | [MIT](https://github.com/langchain-ai/langchain-mcp-adapters/blob/main/LICENSE) |
| 12 | langchain-openai | No | LangChain, Inc. | [LangChain OpenAI](https://github.com/langchain-ai/langchain/tree/master/libs/partners/openai) | [MIT](https://github.com/langchain-ai/langchain/blob/master/libs/partners/openai/LICENSE) |
| 13 | litellm | No | Berri AI | [LiteLLM](https://github.com/BerriAI/litellm) | [MIT for the SDK; separate terms for enterprise code](https://github.com/BerriAI/litellm/blob/main/LICENSE) |
| 14 | lotus-ai | No | LOTUS contributors | [LOTUS](https://github.com/lotus-data/lotus) | [Apache 2.0](https://github.com/lotus-data/lotus/blob/main/LICENSE) |
| 15 | mcp | No | Anthropic, PBC | [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | [MIT](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE) |
| 16 | pandas | No | AQR Capital Management, LLC; Lambda Foundry, Inc.; PyData Development Team; open source contributors | [pandas](https://github.com/pandas-dev/pandas) | [BSD 3-Clause](https://github.com/pandas-dev/pandas/blob/main/LICENSE) |
| 17 | pydantic | No | Pydantic Services Inc. and individual contributors | [Pydantic](https://github.com/pydantic/pydantic) | [MIT](https://github.com/pydantic/pydantic/blob/main/LICENSE) |
| 18 | pyright | No | Microsoft Corporation | [Pyright](https://github.com/microsoft/pyright) | [MIT](https://github.com/microsoft/pyright/blob/main/LICENSE.txt) |
| 19 | pytest | No | Holger Krekel and others | [pytest](https://github.com/pytest-dev/pytest) | [MIT](https://github.com/pytest-dev/pytest/blob/main/LICENSE) |
| 20 | pytest-asyncio | No | pytest-asyncio contributors | [pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio) | [Apache 2.0](https://github.com/pytest-dev/pytest-asyncio/blob/main/LICENSE) |
| 21 | python-dotenv | No | Saurabh Kumar; Ted Tieken; Jacob Kaplan-Moss | [python-dotenv](https://github.com/theskumar/python-dotenv) | [BSD 3-Clause](https://github.com/theskumar/python-dotenv/blob/main/LICENSE) |
| 22 | regex | No | Matthew Barnett; Secret Labs AB (CPython-derived portions) | [regex](https://github.com/mrabarnett/mrab-regex) | [Apache 2.0; CNRI Python 1.6 for CPython-derived portions](https://github.com/mrabarnett/mrab-regex/blob/hg/LICENSE.txt) |
| 23 | rich | No | Will McGugan | [Rich](https://github.com/Textualize/rich) | [MIT](https://github.com/Textualize/rich/blob/main/LICENSE) |
| 24 | ruff | No | Charles Marsh and the upstream contributors identified in the license | [Ruff](https://github.com/astral-sh/ruff) | [MIT and included third-party notices](https://github.com/astral-sh/ruff/blob/main/LICENSE) |
| 25 | setuptools | No | Python Packaging Authority and contributors | [setuptools](https://github.com/pypa/setuptools) | [MIT](https://github.com/pypa/setuptools/blob/main/LICENSE) |
| 26 | tenacity | No | Julien Danjou; Elisey Zanko; Étienne Bersac; Joshua Harlow; Ray Holder | [Tenacity](https://github.com/jd/tenacity) | [Apache 2.0](https://github.com/jd/tenacity/blob/main/LICENSE) |
| 27 | twine | No | Donald Stufft and individual contributors | [Twine](https://github.com/pypa/twine) | [Apache 2.0](https://github.com/pypa/twine/blob/main/LICENSE) |
| 28 | wheel | No | Daniel Holth and contributors | [wheel](https://github.com/pypa/wheel) | [MIT](https://github.com/pypa/wheel/blob/main/LICENSE.txt) |
