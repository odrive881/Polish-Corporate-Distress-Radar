---
title: Distress radar — methodology and data quality
---

This site describes a research pipeline that estimates the probability that a Polish construction
company (`sp. z o.o.`) enters bankruptcy, restructuring or liquidation within 12 or 24 months. It
reads statutory financial statements filed in the Repozytorium Dokumentów Finansowych, registry
history from the KRS, and notices in the Monitor Sądowy i Gospodarczy, and evaluates every model
strictly out of time.

<Alert status="warning">

**What this site does not show.** No company is named or scored here, and no rate for the sector,
a region or a division. The companies the models were built on come from purposive lists, chosen
partly by distress status, so their share of distressed companies is not the sector's, and no
number below is a performance claim about the population.

</Alert>

## Pages

- [Data quality](/data-quality): how the filed statements parse and how their accounting identities
  hold, by structure version and fiscal year.
- [Pipeline coverage](/coverage): how many companies of each list reached each stage of the pipeline.
- [Backtest](/backtest): the pooled out-of-time metrics of each model, with event counts and
  reliability tables.

## Small cells

Every table counts the distinct companies behind each cell. A cell covering fewer companies than
the threshold below shows no figures, and is marked suppressed rather than left out.

## Provenance

Every figure on this site is regenerable from these identifiers.

```sql provenance
select identifier, value from publish.provenance order by identifier
```

<DataTable data={provenance} rows=all />
