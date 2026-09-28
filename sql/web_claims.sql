
select 
wc.contractid,
wc.customerfleetno,
wc.customerid,
wc.dealerid,
wc.claimno, 
wc.claimdate,

wc.serviceid,
wc.vehicleid,
wc.enduserid,
wc.locnid,

wc.fleetno,
wc.repairloc,
wc.causeid, -- int_wccause
    WC_CAUSE.DESCR as cause_description,    

wc.faultid, -- int_wcfault
    WC_FAULT.DESCR AS FAULT_DESCRIPTION,
    
wc.status, -- int_wcstatus
    WC_STATUS.DESCR AS STAUTS_DESCRIPTION, 
    WC_STATUS.COMMENTS AS STATUS_COMMENT, 

wc.jobtype, --int_wcjobtype
    WC_JOBTYPE.DESCR as job_type_description,

wc.commentint,
wc.commentext,
wc.commentunit,
wc.dcomment,
wc.rechargecomment,
wc.diagnosisofwork,

wc.incidentno, 
wc.completionno, 

wc.invoiceno,
wc.invoicedby,

wc.invoicedt,
wc.bulletinno,

wc.jobstart,
wc.jobend,

wc.jobsheets,
wc.attachment,

wc.service, --
wc.mismatch,

wc.dddistance,

wc.runninghours,
wc.standbyhours,
wc.nhours,
wc.ohours,
wc.phours,
wc.sonhours,

wc.apprejby,
wc.apprejdt,

wc.wstartdt,
wc.dealerref,
wc.failcat,
wc.pcofline,
wc.submiton,

wc.currency,

wc.ddunit,
wc.rechargecustomerid,

wc.invoicetocustomerid,
wc.isicclaim,
wc.vehiclebrand,


FROM REF_DB.ECARE_CTE_STG.INT_WEBCLAIM AS WC
LEFT JOIN REF_DB.ECARE_CTE_STG.INT_WCSTATUS AS WC_STATUS ON WC.STATUS = WC_STATUS.STATUS
LEFT JOIN REF_DB.ECARE_CTE_STG.INT_WCCAUSE AS WC_CAUSE ON WC.CAUSEID = WC_CAUSE.CAUSEID 
LEFT JOIN REF_DB.ECARE_CTE_STG.INT_WCFAULT AS WC_FAULT ON WC.FAULTID = WC_FAULT.FAULTID AND WC_FAULT.ISCURRENT=true
LEFT JOIN REF_DB.ECARE_CTE_STG.INT_WCJOBTYPE AS WC_JOBTYPE ON WC.JOBTYPE = WC_JOBTYPE.JOBTYPE

order by claimdate, contractid,  claimno
;