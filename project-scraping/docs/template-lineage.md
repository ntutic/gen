# Template lineage and ownership

Generation writes `.gen-manifest.json` with the source repository/revision (null
when Git metadata is unavailable), source-tree and generator hashes, selected
names, and the post-render hash/ownership of every generated file. It includes
post-lint output when the generator runs Ruff. The manifest does not hash itself.
An uncommitted template edit is represented by content hashes even when the Git
revision still points to the previous commit.

`shared` means infrastructure expected to stay close to upstream. `extension`
marks deliberate customization points such as the project contract and
validation function. `project` marks the source roster, source implementation,
example fixture and public product UI. Ownership is guidance, not a prohibition
on editing copied code.

Keep the standalone-copy model. A consumer is never regenerated over its product
code to obtain a helper fix. Compare the relevant file to its recorded hash,
review the upstream change, port the helper **with its tests and recipe**, then
run source regression/replay tests and the full suite. Document adopted fixes in
the consumer's ordinary commit history. No updater, package registry, release
service or cross-project rollout framework is introduced.

Generator-only extraction plans and generation tests do not become tasks in each
consumer. New projects receive the reusable skills, recipes and runtime tests,
not the history of building this template.
