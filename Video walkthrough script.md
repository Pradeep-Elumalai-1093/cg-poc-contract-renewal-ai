
# How do we achieve pro-activeness:

Recommendation engine - Runs daily & make recommendation for the account manager / dealers. New recommendations are triggered only when the contract expiration date moves from one window to another (Say 90day to 60 day before the contract expires)


# Renewal Prioritization:

gives an overall view of "Global" contract

Each contract is bucketed based on different "Contract expiration days". Example of bucket: contract expiring between 90 days to 61 days will be under <=90 days bucket. Similarly we have <=60 days, <=45 days, <=30 days, <= 10days and Contracts expired as lost contracts

- Each `contract expiring in` card can be used to filter the worklist table.


Contracts

## Matrix Quadrant

Each dot is a contract separated by Risk score & Value of the contract.


## Portfolio KPIs

Changes based on the region, channel & contract expiration filters




---

introducing contract renewal portal -current renewal rate - Key aspects impacting renewal rate - 
    Portal enables tracking out come and Single source of truth

Technical details: (10-15 seconds)
    2 buckets:
        Dashboard = 
        Renewal Prioritization
    Visual represenation of contract


+ ~~Table - Column sort~~
+ Search
+ ~~Section wise Minimize & maximize~~
+ 