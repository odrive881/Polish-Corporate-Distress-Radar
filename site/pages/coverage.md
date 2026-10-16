---
title: Pipeline coverage
---

How many companies of each list reached each stage. A company is modelled only once every statement
it filed has been fetched: to a model, a company whose filings were never fetched looks like one
that never filed.

<Alert status="warning">

The lists are purposive (a hand-picked seed, and a list built by hand with distress status among
its filters). Their composition is not the sector's.

</Alert>

```sql stages
select list, stage_order, stage, entities, suppressed
from publish.pipeline_coverage
order by list, stage_order
```

<DataTable data={stages} rows=all groupBy=list>
  <Column id=stage title="Stage" />
  <Column id=entities title="Companies" />
  <Column id=suppressed title="Suppressed" />
</DataTable>

Stages: **listed** on the list; **identified** in the statistical register (GUS); **searched on
RDF**, the filing portal; **statements stored**, at least one statement's bytes kept;
**statements parsed** into the canonical model; **labelled** with outcomes from the registry;
**modelled**, in the backtest's population.
