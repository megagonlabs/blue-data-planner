## The fragment

{{ instruction }}

## The operator

{{ operator_description }}

{% if operator_limitations %}
What it cannot do: {{ operator_limitations }}
{% endif %}

{% if scan_sources %}
## The sources this fragment scans

A parameter naming a source takes the catalog path exactly as it is spelled here.

{{ scan_sources }}
{% endif %}

{% if attribute_views %}
## The attributes the scanned entities hold

Every attribute of each, with its most frequent values spelled the way the data spells them.
The values and counts are a sample: `and N more` counts the values not listed, and each count is
taken over the rows it states, which can be fewer than the entity holds. How many rows an entity
holds is the row count beside its path above.

{{ attribute_views }}
{% endif %}

{% if input_tables %}
## The tables this fragment was handed

{{ input_tables }}
{% endif %}

Write the parameters.
