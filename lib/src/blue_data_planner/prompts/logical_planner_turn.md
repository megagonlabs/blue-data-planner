{% if request %}
## The request

{{ request }}

## The registered sources

{{ catalog_preview }}
{% endif %}

{% if catalog_context %}
## What the searches found

Each `DISCOVER` node below is listed with the terms it searched for and the entities they
matched. Each entity carries every attribute it holds. Declare column names from these, and name a
`SCAN`'s source by one of the paths below, copied exactly.

A node that found no entity means the catalog holds nothing those terms match. Search again with
different terms in a new `DISCOVER` node, or write that part of the plan from what you already
know.

{{ catalog_context }}
{% endif %}

{% if carried_nodes %}
## What already ran

These nodes stay in the plan. Name the id of one holding rows to read those rows; do not write
any of them again. A `DISCOVER` node has already searched, and what it found is above. The rows
under a node are the first of the rows it holds, and their keys are the column names those rows
really carry.

{{ carried_nodes }}
{% endif %}

{% if replan_reason %}
## Why the last pass ended

{{ replan_reason }}
{% endif %}

Write the plan.
