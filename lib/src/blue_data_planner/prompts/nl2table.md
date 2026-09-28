Write a Python program that produces one table from the tables already in scope.

## What this fragment has to produce

$instruction

## The columns the result has to carry

$output_columns

## The tables in scope

$tables

## The rules the program obeys

- A table is a list of dicts, one dict per row. Assign the answer to a module-level `result`,
  which has to be a list of dicts.
- Every table above is already bound to its name. Nothing is read from a file, a database, or a
  network: everything the program can use is listed above.
- These functions are in scope and may be called: `filter_rows(table, predicate)`,
  `project_columns(table, columns)`, `expand_rows(table, column, into, separator)`,
  `aggregate_rows(table, group_by, aggregations)`, `distinct_rows(table, columns)`,
  `sort_rows(table, keys, limit)`, `join_tables(left, right, on, how)`,
  `union_tables(left, right)`. Ordinary Python — comprehensions, string methods, `sorted` — is
  equally available, and often shorter.
- `join_tables` keeps a key both tables name alike once, and renames any other column both
  tables hold to `<name>_left` and `<name>_right`.
- Values are compared as they are stored. Spell a value the way the sample rows spell it.

Answer with the program and nothing else.
