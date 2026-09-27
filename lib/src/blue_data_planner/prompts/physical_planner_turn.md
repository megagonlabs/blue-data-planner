{% if request -%}
## Request

{{ request }}

{% endif -%}
{% if plan -%}
## Plan

{{ plan }}

{% endif -%}
{% if scanned_entities -%}
## The data the plan scans

{{ scanned_entities }}

{% endif -%}
{% if node_results -%}
## What has run so far

A candidate marked `reuse` keeps what ran and costs nothing. Grouping a node that already ran
throws its result away and pays for the work again; the row counts say how much.

{{ node_results }}

{% endif -%}
{% if failed_fragment -%}
## The fragment that failed

{{ failed_fragment }}

{% endif -%}
{{ candidates }}
