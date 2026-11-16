# Train/test isolation

Core-3K is a subset of Full. Excluding only its current 3,000 IDs does not establish
training independence: related tasks can share the same plant, consultation,
experiment or image.

Each HF release includes cumulative exposure/protection information in `splits/`,
including `protected_core_groups.json`. Its connected closure uses existing case
groups, original groups, independent groups, subjects and exact render hashes,
across P1–P5 and components. Retain earlier Core/demo exposure protection too.

Before teacher collection or SFT/RL selection:

1. Load the frozen cumulative protected IDs and group file.
2. Exclude protected isolation/case groups and linked original cases or subjects.
3. Exclude exact-render overlaps; preserve the broader source linkage policy.
4. Record all exclusions before collecting trajectories.
5. Split retained training examples by isolation group, never by individual turn.

The actual internal split uses the first eight hex digits of SHA-256(group),
modulo 1,000, assigning values below 30 to validation. It yielded 6,741 train and
224 validation trajectories. This happens **after** Core exclusion.
`build_dataset` alone does not certify that an input set is isolated from Core.

`plantbench/preparation/group_audit.py` preserves Full group checks. SFT conversion
retains per-row provenance and isolation groups. Shared environmental records and
reference material can have broader scope than one plant; different task IDs
alone do not establish independence.
