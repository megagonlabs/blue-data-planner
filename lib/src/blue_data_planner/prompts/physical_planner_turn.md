{% if request -%}
## Request

{{ request }}

{% endif -%}
{% if plan -%}
## Plan

{{ plan }}

{% endif -%}
{% if catalog_context -%}
## The data the searches found

{{ catalog_context }}

{% endif -%}
{% if node_results -%}
## What has already run

A candidate marked `reuse` keeps what ran and costs nothing. Grouping a node that already ran
throws its result away and pays for the work again; the row counts say how much.

{{ node_results }}

{% endif -%}
{% if reason -%}
## Why the last attempt failed

{{ reason }}

{% endif -%}
{{ candidates }}
