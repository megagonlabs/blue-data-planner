{% if request %}
## The request

{{ request }}

## The registered sources

{{ catalog_preview }}
{% endif %}

{% if catalog_context %}
## What the searches found

Each entity below carries every attribute it holds. Declare column names from these.

{{ catalog_context }}
{% endif %}

{% if carried_nodes %}
## What already ran

These nodes stay in the plan. Name the id of one holding rows to read those rows; do not write
any of them again. A `DISCOVER` node has already searched, and what it found is above.

{{ carried_nodes }}
{% endif %}

{% if reason %}
## Why the last pass ended

{{ reason }}
{% endif %}

Write the plan.
