----- DADP for PCR AI -----

/// Contract ///

select
    // Contract IDs
    ctr.contractid,
    ctr.contractid_old,
    ctr.jdecontractno,
    ctr.jdecontractversion,
    ctr.contractrefno, 
    ctr.unitid,
    ctr.customerid,
    ctr_status.descr as contract_status,
    ctr.iscurrent as is_active_contract_version,
    ctr.comments as contract_comments,
    // SLA
    ctr.goldencold as is_golden_or_cold, // True is Gold, False = Cold
    ctr_nationality.valid as contract_valid_nationality,
    ctr_type.type as contract_type_desc,
    ctr.serviceinterval as contract_service_interval,
    ctr.travel as includes_travel_charges,
    ctr.servicegen as is_general_service_include,
    ctr.direct is_direct_contract, // True means Direct channel / Third party
    
    ctr_type.allowmaint as contract_type_allow_maint,
    ctr_type.allowrepair as contract_type_allow_repair,
    ctr_type.fieldtest as contract_type_field_test,
    ctr_type.allowsponsorship as contract_type_allow_sponsorship,
    
    // Contract working usage
    ctr.standbyhours,
    ctr.runninghours,
    (ctr.runninghours + ctr.standbyhours) * (coalesce(ctr.duration, 1)/12) as total_hours,

    // Contract Date
    ctr.startdate as contract_start_date,
    ctr.enddate as contract_end_date,
    ctr.origstartdate as contract_original_start_date,
    ctr.origenddate as contract_original_end_date,
    ctr.duration as contract_duration_months,
    // Pricing
    ctr_interval.interval as contract_interval_desc,
    ctr.price as contract_price,
    ctr.sponsoredamt as sponsored_amount,
    ctr.sponsoreddur as sponsored_duration,
    ctr.currency,

    // A
    ctr_cust.accountid, 
    ctr_acc.account, 
    
    // Contact details
    ctr.customerid,
    ctr_cust.addressid,
    ctr_cust.company,
    ctr_cust.contactid,
    ctr_cust.administrator,
    ctr_cust.prodsupport,
    ctr_cust.controller,
    ctr_cust.manager,
    ctr_cust.countryid,
    ctr_addr.address,
    ctr_addr.town,
    ctr_addr.county,
    ctr_addr.country,
    ctr_addr.postcode,
    ctr_addr.phoneopen,
    ctr_addr.phoneclosed,
    ctr_addr.fax,
    ctr_addr.country,
    ctr_addr.phoneclosed,

    ctr_unit.modelid,
    ctr_unit.additions,
    ctr_unit.customerfleetno,
    ctr_unit.userid,
    ctr_unit.userfleetno,
    ctr_unit.locationid,
    ctr_unit.salesorderno,
    ctr_unit.salesinvoiceno,
    ctr_unit.unitcost,
    ctr_unit.exfrancedate,
    ctr_unit.despatchdate,
    ctr_unit.servicedate,
    ctr_unit.locationstartdate,
    ctr_unit.manufacturedate,
    ctr_unit.manufacturingsite,
    ctr_unit.vehicleid,
    ctr_unit.jdeitemnumber,
    ctr_unit.chassisnumber,
    ctr_unit.bodynumber,
    ctr_unit.vehiclebrand,
    // Model details
    
    ctr_model.modelid,
    ctr_model.modelgroup,
    ctr_model.model as model_name,
    ctr_model.description as model_description,
    ctr_model_cat.category as model_category,
    ctr_model_type.type as model_type,
    ctr_mfg.manufacturer as model_manufacturer,
    ctr_ref_type.refrigeranttype as model_refrigerant_type,


// Contract
from ref_db.ecare_cte_stg.int_contract as ctr
left join ref_db.ecare_cte_stg.int_contractvalid as ctr_nationality on ctr_nationality.contractvalidid=ctr.contractvalidid
left join ref_db.ecare_cte_stg.int_contracttype as ctr_type on ctr_type.contracttypeid=ctr.contracttypeid
left join ref_db.ecare_cte_stg.int_contractinterval as ctr_interval on ctr_interval.contractintervalid=ctr.intervalid
left join ref_db.ecare_cte_stg.int_contractstatus as ctr_status on ctr_status.code=ctr.status
// Customer
left join ref_db.ecare_cte_stg.int_customer as ctr_cust on ctr_cust.customerid=ctr.customerid
left join ref_db.ecare_cte_stg.int_address as ctr_addr on ctr_addr.addressid=ctr_cust.addressid
left join ref_db.ecare_cte_stg.int_account as ctr_acc on ctr_acc.accountid=ctr_cust.accountid
// Unit
left join ref_db.ecare_cte_stg.int_unit as ctr_unit on ctr_unit.unitid = ctr.unitid and ctr_unit.iscurrent=True
// Model
left join ref_db.ecare_cte_stg.int_model as ctr_model on ctr_model.modelid = ctr_unit.modelid
left join ref_db.ecare_cte_stg.int_modelcategory as ctr_model_cat on ctr_model_cat.modelcategoryid=ctr_model.modelcategoryid
left join ref_db.ecare_cte_stg.int_modeltype as ctr_model_type on ctr_model_type.modeltypeid=ctr_model.modeltypeid
left join ref_db.ecare_cte_stg.int_manufacturer as ctr_mfg on ctr_mfg.manufacturerid=ctr_model.manufacturerid
left join ref_db.ecare_cte_stg.int_refrigeranttype as ctr_ref_type on ctr_ref_type.refrigeranttypeid=ctr_model.refrigeranttypeid
;
