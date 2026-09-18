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

Every attribute of each, with a few of its values spelled the way the data spells them.
Write filters and comparisons against these spellings, not against the words in the request.

{{ attribute_views }}
{% endif %}

{% if input_tables %}
## The tables this fragment was handed

{{ input_tables }}
{% endif %}

Write the parameters.
