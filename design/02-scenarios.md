# $PROJECT_NAME$ — Scenarios

<!--
Provide a number of scenarios illustrating potentiual use cases step by step. 
-->

<!-- 
Examples:

## Scenario 1: File Upload as Data Source

A user uploads a resume. The file is ingested and stored as a **file data source**, making all content within it available for future querying. Agents can subsequently query any information contained in the resume — work history, skills, education, contact details — without the user re-providing it.

## Scenario 2: Conversation as Streaming Data Source

A user engages in an agentic conversation. New data in the conversation is continuously ingested and stored as a **streaming data source**. As the conversation progresses, the full content up to date remains available for future querying across sessions, preserving context without requiring the user to repeat themselves.

## Scenario 3: Entity Extraction from Conversation

As a conversation unfolds, UserDb can optionally extract structured entities from user , if an additional (filter) query is provided, and stores query results as **structured data**. These entities are available for querying.

**Example:**
> User: "I recently moved to New York for my job at Google."

Extracted and stored:
```
location → New York
employer → Google
```

## Scenario 4: Natural Language Query — Direct Retrieval

An agent queries UserDb in natural language. UserDb resolves the query against all available data sources and returns structured results.

**Example:**
> Agent query: "What is the user's location?"
> USERDb Response to agent: `location: New York`

## Scenario 5: Natural Language Query — Confirmation Interaction

An agent query may trigger a user interaction when the stored value needs confirmation (e.g., the data may be stale or ambiguous). UserDb surfaces the stored value and prompts the user to verify, through the most appropriate contact methods available, either asynchronously or synchronously. 

**Example:**
> Agent query: "What is the user's location?"
(internally UserDb) obtains `location: New York` *(stored value)*
(internally) UserDb asks User (e.g. through SMS): "Are you still in New York?"
User confirms or corrects → UserDb updates location data and metadata accordingly.
> USERDb Response to agent: `location: New York`

## Scenario 6: Natural Language Query — Elicitation and Aggregation

An agent query may reveal gaps in stored data. UserDb detects what is known, formulates a targeted question to elicit the missing information, extracts new data from the user's response, stores it, and returns an aggregated result.

**Example:**
> Agent query: "What programming language skills do you have?"
(internally UserDb) obtains `programming languages: Python, Java` *(stored value)*
(internally) UserDb asks User (e.g. through SMS): "Besides Python and Java, does the user have any recent programming language skills?"
User adds C → UserDb updates programming languages data and metadata accordingly.
> USERDb Response to agent: `programming languages: Python, Java, C`
-->