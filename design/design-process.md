# Names

Start by asking project name (PROJECT_NAME) and library name (LIB_NAME), and store them in .envrc file as environment variable.

Project name should be short but meaningful, preferrably single word but two words is okay.
Library name should confirm to the following convention. Each library should start with `blue-` as prefix. If project is related to data it should follow with `data-`. More specific categorization should be separated by `-`.

# Design Process

For new projects, work through the following design documents in order, creating each one before moving to implementation.

For existing projects, also work through the design documents, creating any missing ones. Work with the developer to fill what is missing and restructure the repo content and directory organization.

## Document Sequence

1. **Overview** — objective, what the system does, design philosophy
2. **Scenarios** — use cases describing how the system is used in practice
3. **High Level Architecture** — key components and their interfaces
4. **Detailed Architecture** — flow of information between key components
5. **Software Stack** — languages, frameworks, and key dependencies
6. **Library** — key classes and functions
7. **Repo Organization** — file structure of the repository
8. **Coding Style** — language, formatting, and conventions
9. **API / Interface Spec** — external-facing APIs (REST, CLI, etc.)
10. **Data Model** — schema definitions, entity relationships, data flow
11. **Testing Strategy** — what to test, at what level, and how
12. **Deployment / Ops** — environment setup, configuration, infrastructure
13. **Decision Log (ADR)** — architectural decisions and their rationale

## Guidelines

- Create documents one at a time, in order
- Each document informs the next — do not skip ahead
- Store all design documents in the `design/` directory at the repo root
- Name files with a numeric prefix matching the sequence above (e.g. `01-overview.md`)
- Create the decision log file at the start of design (step 1) and add entries throughout; do not wait until the end
- When iterative coding begins, suggest libraries and other software for the component under development and confirm choices before writing any code

## Review Protocol (per document)

After receiving user input for each document:

1. **Completeness review** — assess the input and suggest anything missing or underspecified before finalizing the document
2. **Integrity check** — after the document is written, verify consistency with all previously completed documents; flag any discrepancies, contradictions, or gaps to the user before proceeding to the next step
