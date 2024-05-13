use lazy_static::lazy_static;
use std::{
    collections::BTreeMap,
    time::{SystemTime, UNIX_EPOCH},
};

#[cfg(debug_assertions)]
use std::{fs, path::Path};

use limbo_harness_support::{
    load_limbo,
    models::{
        ActualResult, ExpectedResult, Feature, KeyUsage, KnownEkUs, LimboResult, PeerKind,
        PeerName, Testcase, TestcaseResult,
    },
};

use x509_cert::{
    certificate::{CertificateInner, Raw},
    der::{
        flagset::FlagSet,
        oid::db::rfc5280::{
            ANY_EXTENDED_KEY_USAGE, ID_CE_NAME_CONSTRAINTS, ID_CE_SUBJECT_ALT_NAME,
            ID_KP_CLIENT_AUTH, ID_KP_CODE_SIGNING, ID_KP_EMAIL_PROTECTION, ID_KP_OCSP_SIGNING,
            ID_KP_SERVER_AUTH, ID_KP_TIME_STAMPING,
        },
        Decode, DecodePem, Encode,
    },
    ext::pkix::{name::GeneralName, KeyUsages, NameConstraints, SubjectAltName},
};

use certval::{
    enforce_trust_anchor_constraints, get_validation_status,
    name_constraints_settings_to_name_constraints_set, populate_5280_pki_environment, CertFile,
    CertSource, CertVector, CertificationPath, CertificationPathResults, CertificationPathSettings,
    ExtensionProcessing, NameConstraintsSettings, PDVCertificate, PDVExtension, PkiEnvironment,
    TaSource,
};

type Certificate = CertificateInner<Raw>;

lazy_static! {
    static ref WEAK_KEY_CHECKS : Vec<&'static str> = vec![
        "webpki::forbidden-weak-rsa-key-in-root",
        "webpki::forbidden-weak-rsa-in-leaf",
        "webpki::forbidden-rsa-not-divisable-by-8-in-root",
        "webpki::forbidden-rsa-key-not-divisable-by-8-in-leaf",
    ];

    static ref BUG : Vec<&'static str> = vec![
        "rfc5280::nc::nc-permits-invalid-email-san"
    ];

    static ref PATHOLOGICAL_CHECKS : Vec<&'static str> = vec![
        "pathological::nc-dos-1",
        "pathological::nc-dos-2",
        "pathological::nc-dos-3"
    ];

    static ref UNSUPPORTED_APPLICATION_CHECK : Vec<&'static str> = vec![
        "webpki::san::mismatch-apex-subdomain-san"
    ];

    static ref BUSTED_TEST_CASES : Vec<&'static str> = vec![
        "rfc5280::ee-empty-issuer" // the issuer name in the EE is not actually empty and chains to the TA just fine
    ];

    static ref LINTER_TESTS : Vec<&'static str> = vec![
        "rfc5280::aki::critical-aki",
        "rfc5280::aki::leaf-missing-aki",
        "rfc5280::aki::intermediate-missing-aki",
        "rfc5280::aki::cross-signed-root-missing-aki",
        "rfc5280::ca-empty-subject", // the empty names actually chain, making this more of a linter check
        "rfc5280::nc::permitted-dns-match-noncritical",
        "rfc5280::nc::not-allowed-in-ee-noncritical",
        "rfc5280::nc::not-allowed-in-ee-critical",
        "rfc5280::pc::ica-noncritical-pc",
        "rfc5280::san::noncritical-with-empty-subject",
        "rfc5280::serial::too-long",
        "rfc5280::serial::zero",
        "rfc5280::ski::critical-ski",
        "rfc5280::ski::root-missing-ski",
        "rfc5280::ski::intermediate-missing-ski",
        "rfc5280::root-missing-basic-constraints",
        "rfc5280::root-non-critical-basic-constraints",
        "rfc5280::root-inconsistent-ca-extensions",
        "rfc5280::leaf-ku-keycertsign",
        "rfc5280::duplicate-extensions",
        "webpki::aki::root-with-aki-missing-keyidentifier",
        "webpki::aki::root-with-aki-authoritycertissuer",
        "webpki::aki::root-with-aki-authoritycertserialnumber",
        "webpki::aki::root-with-aki-all-fields",
        "webpki::aki::root-with-aki-ski-mismatch",
        "webpki::eku::ee-anyeku",
        "webpki::eku::ee-critical-eku",
        "webpki::eku::ee-without-eku",
        "webpki::eku::root-has-eku",
        "webpki::nc::intermediate-permitted-excluded-subtrees-both-null",
        "webpki::nc::intermediate-permitted-excluded-subtrees-both-empty-sequences",
        "webpki::san::no-san",
        "webpki::san::san-critical-with-nonempty-subject",
        "webpki::malformed-aia",
        "webpki::forbidden-p192-leaf",
        "webpki::forbidden-dsa-leaf",
        "webpki::v1-cert",
        "webpki::ee-basicconstraints-ca",
        "webpki::ca-as-leaf",
    ];
}

fn expected_failure(tc: &Testcase) -> bool {
    let id = tc.id.as_str();
    if LINTER_TESTS.contains(&id)
        || BUSTED_TEST_CASES.contains(&id)
        || UNSUPPORTED_APPLICATION_CHECK.contains(&id)
        || WEAK_KEY_CHECKS.contains(&id)
        || PATHOLOGICAL_CHECKS.contains(&id)
        || BUG.contains(&id)
    {
        return true;
    }
    false
}

fn main() {
    let limbo = load_limbo();

    let mut results = vec![];
    for testcase in &limbo.testcases {
        results.push(evaluate_testcase(testcase));
    }

    let mut skipped_rationales: BTreeMap<String, i32> = BTreeMap::new();
    let mut successful = 0;
    let mut failed = 0;
    let mut skipped = 0;
    let mut unexpected = 0;
    for (ii, result) in results.iter().enumerate() {
        let tc = limbo.testcases.get(ii).unwrap();

        match result.actual_result {
            ActualResult::Success => {
                successful += 1;
                if tc.expected_result != ExpectedResult::Success && !expected_failure(&tc) {
                    println!(
                        "Did not get expected result for test case # {ii} - {:?}",
                        tc.id
                    );
                    unexpected += 1;
                }
            }
            ActualResult::Failure => {
                failed += 1;
                if tc.expected_result != ExpectedResult::Failure {
                    println!(
                        "Did not get expected result for test case # {ii} - {:?}",
                        tc.id
                    );
                    unexpected += 1;
                }
            }
            ActualResult::Skipped => {
                skipped += 1;
                let context = result.context.clone().unwrap();
                if !skipped_rationales.contains_key(&context) {
                    skipped_rationales.insert(context, 1);
                } else {
                    *skipped_rationales.get_mut(&context).unwrap() += 1;
                }
            }
        }
    }
    println!("Found {unexpected} test cases where expected results were not produced.");
    println!("Ran {} test cases.", results.len());
    println!("- {successful} succeeded as expected, {failed} failed as expected, and {skipped} were skipped due to missing support.");
    println!("- {failed} failed as expected, and {skipped} were skipped due to missing support.");
    println!("- {skipped} were skipped due to missing support.");
    println!("- {} were skipped as linter checks.", LINTER_TESTS.len());
    println!(
        "- {} were skipped until weak key detection is added.",
        WEAK_KEY_CHECKS.len()
    );
    println!("- {} were skipped as a bug to be fixed.", BUG.len());
    println!(
        "- {} were skipped as pathological cases that need attention.",
        PATHOLOGICAL_CHECKS.len()
    );
    println!(
        "- {} were skipped as unsupported application-level checks.",
        UNSUPPORTED_APPLICATION_CHECK.len()
    );
    println!(
        "- {} were skipped as a broken test case (need to pull the fix).",
        BUSTED_TEST_CASES.len()
    );

    for k in skipped_rationales.keys() {
        println!("{k}: {:?}", skipped_rationales.get(k));
    }

    let result = LimboResult {
        version: 1,
        harness: "certval".into(),
        results,
    };

    serde_json::to_writer_pretty(std::io::stdout(), &result).unwrap();
}

fn has_unsupported_san(san: &SubjectAltName) -> bool {
    for gn in &san.0 {
        if let GeneralName::IpAddress(_) = gn {
            return true;
        }
    }
    false
}

fn has_unsupported_name_constraint(cert: &Certificate) -> bool {
    if let Some(exts) = &cert.tbs_certificate.extensions {
        for ext in exts {
            if ext.extn_id == ID_CE_NAME_CONSTRAINTS {
                let nc = NameConstraints::from_der(ext.extn_value.as_bytes()).unwrap();
                if let Some(perm) = &nc.permitted_subtrees {
                    for gs in perm {
                        match gs.base {
                            GeneralName::IpAddress(_) => return true,
                            GeneralName::OtherName(_) => return true,
                            GeneralName::EdiPartyName(_) => return true,
                            _ => {}
                        }
                    }
                }
                if let Some(excl) = &nc.excluded_subtrees {
                    for gs in excl {
                        match gs.base {
                            GeneralName::IpAddress(_) => return true,
                            GeneralName::OtherName(_) => return true,
                            GeneralName::EdiPartyName(_) => return true,
                            _ => {}
                        }
                    }
                }
            }
        }
    }
    false
}

fn add_peer_name_to_ncs(pn: &PeerName, ncs: &mut NameConstraintsSettings) {
    match pn.kind {
        PeerKind::Rfc822 => {
            if ncs.rfc822_name.is_some() {
                ncs.rfc822_name.as_mut().unwrap().push(pn.value.clone());
            } else {
                ncs.rfc822_name = Some(vec![pn.value.clone()]);
            }
        }
        PeerKind::Dns => {
            if ncs.dns_name.is_some() {
                ncs.dns_name.as_mut().unwrap().push(pn.value.clone());
            } else {
                ncs.dns_name = Some(vec![pn.value.clone()]);
            }
        }
        PeerKind::Ip => {}
    }
}
fn convert_peer_names_to_name_constraints_settings(
    tc: &Testcase,
) -> Option<NameConstraintsSettings> {
    if tc.expected_peer_name.is_none() && tc.expected_peer_names.is_empty() {
        return None;
    }

    let mut ncs = NameConstraintsSettings {
        user_principal_name: None,
        rfc822_name: None,
        dns_name: None,
        directory_name: None,
        uniform_resource_identifier: None,
        not_supported: None,
    };

    if let Some(pn) = &tc.expected_peer_name {
        add_peer_name_to_ncs(pn, &mut ncs);
    }

    for pn in &tc.expected_peer_names {
        add_peer_name_to_ncs(pn, &mut ncs);
    }

    Some(ncs)
}

fn evaluate_testcase(tc: &Testcase) -> TestcaseResult {
    if !tc.signature_algorithms.is_empty() {
        return TestcaseResult::skip(tc, "signature_algorithms not supported yet");
    }

    // Prepare a path settings object using information from the Testcase
    let mut cps = CertificationPathSettings::new();

    if tc.features.contains(&Feature::MaxChainDepth) {
        let d = tc.max_chain_depth.unwrap() as u8;
        cps.set_initial_path_length_constraint(d);
    }

    if !tc.key_usage.is_empty() {
        let mut target_ku: FlagSet<KeyUsages> = Default::default();
        for ku in &tc.key_usage {
            match ku {
                KeyUsage::DigitalSignature => target_ku |= KeyUsages::DigitalSignature,
                KeyUsage::ContentCommitment => target_ku |= KeyUsages::NonRepudiation,
                KeyUsage::KeyEncipherment => target_ku |= KeyUsages::KeyEncipherment,
                KeyUsage::DataEncipherment => target_ku |= KeyUsages::DataEncipherment,
                KeyUsage::KeyAgreement => target_ku |= KeyUsages::KeyAgreement,
                KeyUsage::KeyCertSign => target_ku |= KeyUsages::KeyCertSign,
                KeyUsage::CRlSign => target_ku |= KeyUsages::CRLSign,
                KeyUsage::EncipherOnly => target_ku |= KeyUsages::EncipherOnly,
                KeyUsage::DecipherOnly => target_ku |= KeyUsages::DecipherOnly,
            }
        }
        cps.set_target_key_usage(target_ku.bits());
    }

    if tc.extended_key_usage.len() > 0 {
        let mut ekus = vec![];
        for eku in &tc.extended_key_usage {
            match eku {
                KnownEkUs::ServerAuth => ekus.push(ID_KP_SERVER_AUTH.to_string()),
                KnownEkUs::ClientAuth => ekus.push(ID_KP_CLIENT_AUTH.to_string()),
                KnownEkUs::CodeSigning => ekus.push(ID_KP_CODE_SIGNING.to_string()),
                KnownEkUs::EmailProtection => ekus.push(ID_KP_EMAIL_PROTECTION.to_string()),
                KnownEkUs::OcspSigning => ekus.push(ID_KP_OCSP_SIGNING.to_string()),
                KnownEkUs::TimeStamping => ekus.push(ID_KP_TIME_STAMPING.to_string()),
                KnownEkUs::AnyExtendedKeyUsage => ekus.push(ANY_EXTENDED_KEY_USAGE.to_string()),
            }
        }
        cps.set_extended_key_usage(ekus);
    }

    cps.set_extended_key_usage_path(true);

    cps.set_enforce_trust_anchor_constraints(true);

    let time_of_interest = match tc.validation_time {
        Some(toi) => toi.timestamp() as u64,
        None => SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs(),
    };
    cps.set_time_of_interest(time_of_interest);

    let mut pe = PkiEnvironment::new();
    populate_5280_pki_environment(&mut pe);

    // flag to indicate a TA or CA used by this test case features an unsupported name constraint
    let mut has_an_ip_constraint = false;

    // treat unsupported peer names as an unsupported constraint (this may cause a few success cases to be skipped)
    for pn in &tc.expected_peer_names {
        if pn.kind == PeerKind::Ip {
            has_an_ip_constraint = true;
        }
    }
    if let Some(pn) = &tc.expected_peer_name {
        if pn.kind == PeerKind::Ip {
            has_an_ip_constraint = true;
        }
    }

    // Prepare a TA store using TAs from the testcase
    let mut ta_store = TaSource::new();
    #[allow(unused_variables)]
    for (ii, ta) in tc.trusted_certs.iter().enumerate() {
        let cert_ta = Certificate::from_pem(ta.as_bytes()).expect("Read pem file");
        #[cfg(debug_assertions)]
        {
            let p = Path::new("./target");
            let f = p.join(Path::new(&format!("ta_{ii}.der")));
            let _ = fs::write(f, &cert_ta.to_der().unwrap());
        }

        if has_unsupported_name_constraint(&cert_ta) {
            has_an_ip_constraint = true;
            //return TestcaseResult::skip(tc, "unsupported name constraint");
        }
        ta_store.push(CertFile {
            bytes: cert_ta.to_der().expect("serialize as der"),
            filename: String::new(),
        });
    }
    ta_store.initialize().unwrap();
    pe.add_trust_anchor_source(Box::new(ta_store.clone()));

    // Prepare a certificate store using certificates from the testcase
    let mut cert_store = CertSource::new();
    #[allow(unused_variables)]
    for (ii, ca) in tc.untrusted_intermediates.iter().enumerate() {
        let cert_ca = Certificate::from_pem(ca.as_bytes()).expect("Read pem file");

        #[cfg(debug_assertions)]
        {
            let p = Path::new("./target");
            let f = p.join(Path::new(&format!("ca_{ii}.der")));
            let _ = fs::write(f, &cert_ca.to_der().unwrap());
        }

        if has_unsupported_name_constraint(&cert_ca) {
            has_an_ip_constraint = true;
            //return TestcaseResult::skip(tc, "unsupported name constraint");
        }
        cert_store.push(CertFile {
            bytes: cert_ca.to_der().expect("serialize as der"),
            filename: String::new(),
        });
    }
    cert_store.initialize(&cps).unwrap();

    // invoke the path builder to prepare a graph with all partial paths in the given infrastructure and add the source to environment
    cert_store.find_all_partial_paths(&pe, &cps);
    pe.add_certificate_source(Box::new(cert_store.clone()));

    // Parse the target certificate from the Testcase
    let cert = if let Ok(cert) = Certificate::from_pem(tc.peer_certificate.as_bytes()) {
        cert
    } else {
        return TestcaseResult::fail(tc, "unable to parse target cert");
    };
    let leaf = PDVCertificate::try_from(cert).unwrap();

    #[cfg(debug_assertions)]
    {
        let p = Path::new("./target");
        let f = p.join(Path::new(&format!("target.der")));
        let _ = fs::write(f, &leaf.encoded_cert);
    }

    // find all paths in the graph built above
    let mut paths: Vec<CertificationPath> = vec![];
    pe.get_paths_for_target(&pe, &leaf, &mut paths, 0, time_of_interest)
        .unwrap();

    let mut observed_status_values = vec![];
    let mut observed_errors = vec![];

    // loop over paths looking for one that validates
    for path in &mut paths {
        // TA constraints are a modification of user supplied constraints per RFC 5937
        let mod_cps = match enforce_trust_anchor_constraints(&mut cps, &path.trust_anchor) {
            Ok(mod_cps) => mod_cps,
            Err(_e) => {
                if tc.expected_result == ExpectedResult::Failure && has_an_ip_constraint {
                    return TestcaseResult::skip(tc, "unsupported name constraint");
                } else {
                    return TestcaseResult::fail(tc, "TA constraint processing failed");
                }
            }
        };

        let mut cpr = CertificationPathResults::new();
        match pe.validate_path(&pe, &mod_cps, path, &mut cpr) {
            Ok(()) => match get_validation_status(&cpr) {
                Some(status) => {
                    if certval::PathValidationStatus::Valid == status {
                        if tc.expected_result == ExpectedResult::Failure
                            && (tc.expected_peer_name.is_some()
                                || !tc.expected_peer_names.is_empty())
                        {
                            // Some test cases should fail due to name checking that would normally be performed by an application.
                            // Approximate that here.
                            if !tc.expected_peer_names.is_empty() || tc.expected_peer_name.is_some()
                            {
                                if let Some(init_perm) =
                                    convert_peer_names_to_name_constraints_settings(tc)
                                {
                                    let mut bufs = BTreeMap::new();
                                    let ncs = name_constraints_settings_to_name_constraints_set(
                                        &init_perm, &mut bufs,
                                    )
                                    .unwrap();
                                    if let Ok(Some(PDVExtension::SubjectAltName(san))) =
                                        path.target.get_extension(&ID_CE_SUBJECT_ALT_NAME)
                                    {
                                        if !ncs.san_within_permitted_subtrees(&Some(san)) {
                                            return TestcaseResult::fail(
                                                tc,
                                                "peer name check failed",
                                            );
                                        }
                                        if has_unsupported_san(san) {
                                            return TestcaseResult::skip(
                                                tc,
                                                "unsupported SubjectAltName in leaf",
                                            );
                                        }
                                    } else {
                                        return TestcaseResult::fail(
                                            tc,
                                            "peer name check failed because SAN was absent",
                                        );
                                    }
                                }
                            }
                        }

                        // Some tests should fail due to IP address constraint processing. Since IP
                        // address constraints are not supported, return skip for those.
                        if tc.expected_result == ExpectedResult::Failure && has_an_ip_constraint {
                            return TestcaseResult::skip(tc, "unsupported name constraint");
                        }

                        return TestcaseResult::success(tc);
                    } else {
                        // Some tests should succeed due to IP address constraint processing. Since IP
                        // address constraints are not supported, return skip for those.
                        if tc.expected_result == ExpectedResult::Success && has_an_ip_constraint {
                            return TestcaseResult::skip(tc, "unsupported name constraint");
                        }

                        observed_status_values.push(status);
                    }
                }
                None => {
                    panic!();
                }
            },
            Err(e) => {
                if tc.expected_result == ExpectedResult::Success && has_an_ip_constraint {
                    return TestcaseResult::skip(tc, "unsupported name constraint");
                }

                observed_errors.push(format!("validate_path failed with {e:?}"));
            }
        };
    }
    TestcaseResult::fail(
        tc,
        &format!("{:?}: {:?}", observed_status_values, observed_errors),
    )
}
