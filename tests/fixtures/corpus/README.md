# Fidelity corpus

Twenty real, complex PDFs used to measure conversion quality against the spec's success
criteria. The harness that reads them is [`ops/measure-fidelity.py`](../../../ops/measure-fidelity.py).

**The PDFs are not in this repository.** They are real documents — often large, often
not ours to redistribute — so this directory holds only `manifest.yaml`, which describes
what each document is and what a faithful conversion of it must contain.

## Assembling it

1. Choose 20 documents that resemble what this workgroup actually converts. The traits
   the corpus must cover as a whole are listed at the bottom of `manifest.yaml`.
2. Put them in this directory.
3. For each, fill in an entry in `manifest.yaml`: page count, heading count, table count,
   figure count, and a few `must_contain` strings.

Counting by hand is the tedious part and also the point: the counts are the ground truth
the gates are measured against, so they must come from reading the document, not from a
tool's opinion of it.

Choose `must_contain` strings that prove the hard case worked — a sentence that appears
only on a scanned page, a cell from the middle of a table, a line from the second column.
A string that would appear in even a broken conversion proves nothing.

## Running it

```bash
python3 ops/measure-fidelity.py --base-url http://10.0.0.19:8080
```

The gates it reports against:

| Criterion | Gate |
|---|---|
| SC-001 | ≥90% of documents convert on the first attempt |
| SC-002 | ≥95% of headings and ≥90% of tables survive |
| FR-004 | Figures present, in position, with surrounding text intact |

Record the results in `deploy/README.md`. Re-run after any engine version change: an
upgrade that quietly changes layout analysis will show up here and nowhere else.
