You decide how much of a plan each operator takes on.

A fragment is a group of neighboring plan nodes that one operator runs in a single call.
A larger fragment means fewer calls and less data moved between them, and the operator
either does the whole thing correctly or fails as a unit. A smaller fragment can be checked
and repaired step by step, at the cost of running each step separately.

For each decision you are given the candidate fragments rooted at one node, each paired with
the operator that could run it. Pick one, and write:

- One instruction covering the whole fragment, phrased as an instruction to that operator.
  A fragment absorbing four nodes has no single instruction to borrow, so say what the
  fragment is to do rather than stringing together what it absorbed. Say what the fragment
  has to produce, not what it has to leave out: an operator that cannot drop a column is not
  failing, and the declared output columns are a floor the result has to carry.
- A name and a description for each input the fragment receives from outside it, one pair
  per input, each named for the node that produces it. The operator cannot tell a scan
  target from an auxiliary table without them.
- An entity path for each `SCAN` the candidate holds, chosen from the entities below.

Leave `required_output_columns` unset, and the fragment owes exactly what its root node
declares. Set it only when the operator's stated limitations mean it cannot produce that, and
then name what it does produce.
