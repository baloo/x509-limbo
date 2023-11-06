"""
RFC5280 profile tests.
"""

import random
from datetime import datetime
from ipaddress import IPv4Address, IPv4Network

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec

from limbo.assets import _ASSETS_PATH, Certificate, ext
from limbo.models import Feature, KnownEKUs, PeerName
from limbo.testcases._core import Builder, testcase


@testcase
def ee_empty_issuer(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> EE
    ```

    This chain is invalid solely because of the EE cert's construction:
    it has an empty issuer name, which isn't allowed under the RFC 5280 profile.
    """
    # Intentionally empty issuer name.
    issuer = x509.Name([])
    subject = x509.Name.from_rfc4514_string("CN=empty-issuer")
    root = builder.root_ca(issuer=issuer, subject=subject)
    leaf = builder.leaf_cert(root)

    builder = builder.server_validation()
    builder = builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def ca_empty_subject(builder: Builder) -> None:
    """
    Produces an **invalid** chain due to an invalid CA cert.

    The CA cert contains an empty Subject `SEQUENCE`, which is disallowed
    under RFC 5280:

    > If the subject is a CA [...], then the subject field MUST be populated
    > with a non-empty distinguished name
    """

    root = builder.root_ca(subject=x509.Name([]))
    leaf = builder.leaf_cert(root)

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="DNS", value="example.com")
    ).fails()


@testcase
def unknown_critical_extension_ee(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> EE
    ```

    The EE cert has an extension, 1.3.6.1.4.1.55738.666.1, that no implementation
    should recognize. As this unrecognized extension is marked as critical, a
    chain should not be built with this EE.
    """
    root = builder.root_ca()
    leaf = builder.leaf_cert(
        root,
        extra_extension=ext(
            x509.UnrecognizedExtension(x509.ObjectIdentifier("1.3.6.1.4.1.55738.666.1"), b""),
            critical=True,
        ),
    )

    builder = builder.server_validation()
    builder = builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def unknown_critical_extension_root(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> EE
    ```

    The root has an extension, 1.3.6.1.4.1.55738.666.1, that no implementation
    should recognize. As this unrecognized extension is marked as critical, a
    chain should not be built with this root.
    """

    root = builder.root_ca(
        extra_extension=ext(
            x509.UnrecognizedExtension(x509.ObjectIdentifier("1.3.6.1.4.1.55738.666.1"), b""),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(root)

    builder = builder.server_validation()
    builder = builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def unknown_critical_extension_intermediate(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> intermediate (pathlen:0) -> EE
    ```

    The intermediate has an extension, 1.3.6.1.4.1.55738.666.1, that no implementation
    should recognize. As this unrecognized extension is marked as critical, a
    chain should not be built with this intermediate.
    """

    root = builder.root_ca()
    intermediate = builder.intermediate_ca(
        root,
        pathlen=0,
        extra_extension=ext(
            x509.UnrecognizedExtension(x509.ObjectIdentifier("1.3.6.1.4.1.55738.666.1"), b""),
            critical=True,
        ),
    )
    leaf = builder.leaf_cert(intermediate)

    builder = builder.server_validation()
    builder = (
        builder.trusted_certs(root)
        .untrusted_intermediates(intermediate)
        .peer_certificate(leaf)
        .fails()
    )


@testcase
def critical_aki(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> EE
    ```

    The root cert has an AKI extension marked as critical, which is disallowed
    under the [RFC 5280 profile]:

    > Conforming CAs MUST mark this extension as non-critical.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.1
    """
    key = ec.generate_private_key(ec.SECP256R1())
    root = builder.root_ca(
        key=key,
        aki=ext(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=True
        ),
    )
    leaf = builder.leaf_cert(root)

    builder = builder.server_validation()
    builder = builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def self_signed_root_missing_aki(builder: Builder) -> None:
    """
    Produces the following **valid** chain:

    ```
    root -> EE
    ```

    The root cert is missing the AKI extension, which is ordinarily forbidden
    under the [RFC 5280 profile] **unless** the certificate is self-signed,
    which this root is:

    > The keyIdentifier field of the authorityKeyIdentifier extension MUST
    > be included in all certificates generated by conforming CAs to
    > facilitate certification path construction.  There is one exception;
    > where a CA distributes its public key in the form of a "self-signed"
    > certificate, the authority key identifier MAY be omitted.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.1
    """
    root = builder.root_ca(aki=None)
    leaf = builder.leaf_cert(root)

    builder = builder.server_validation()
    builder = builder.trusted_certs(root).peer_certificate(leaf).succeeds()


@testcase
def cross_signed_root_missing_aki(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> EE
    ```

    The root is cross signed by another root but missing the AKI extension,
    which is ambiguous but potentially disallowed under the [RFC 5280 profile].

    > The keyIdentifier field of the authorityKeyIdentifier extension MUST
    > be included in all certificates generated by conforming CAs to
    > facilitate certification path construction.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.1
    """
    xsigner_root = builder.root_ca()
    root = builder.intermediate_ca(xsigner_root, pathlen=0, aki=None)
    leaf = builder.leaf_cert(root)

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def intermediate_missing_aki(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> intermediate -> EE
    ```

    The intermediate is signed by the root but missing the AKI extension, which
    is forbidden under the [RFC 5280 profile].

    > The keyIdentifier field of the authorityKeyIdentifier extension MUST
    > be included in all certificates generated by conforming CAs to
    > facilitate certification path construction.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.1
    """
    root = builder.root_ca()
    intermediate = builder.intermediate_ca(root, pathlen=0, aki=None)
    leaf = builder.leaf_cert(intermediate)

    builder = builder.server_validation()
    builder.trusted_certs(root).untrusted_intermediates(intermediate).peer_certificate(leaf).fails()


@testcase
def leaf_missing_aki(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> EE
    ```

    The EE cert is signed by the root but missing the AKI extension, which is
    forbidden under the [RFC 5280 profile].

    > The keyIdentifier field of the authorityKeyIdentifier extension MUST
    > be included in all certificates generated by conforming CAs to
    > facilitate certification path construction.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.1
    """
    root = builder.root_ca()
    leaf = builder.leaf_cert(root, aki=None)

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def critical_ski(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> EE
    ```

    The root cert has an SKI extension marked as critical, which is disallowed
    under the [RFC 5280 profile].

    > Conforming CAs MUST mark this extension as non-critical.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.2
    """
    key = ec.generate_private_key(ec.SECP256R1())
    root = builder.root_ca(
        ski=ext(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=True),
    )
    leaf = builder.leaf_cert(root)

    builder = builder.server_validation()
    builder = builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def missing_ski(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> EE
    ```

    The root cert is missing the SKI extension, which is disallowed under the
    [RFC 5280 profile].

    > To facilitate certification path construction, this extension MUST
    > appear in all conforming CA certificates, that is, all certificates
    > including the basic constraints extension (Section 4.2.1.9) where the
    > value of cA is TRUE.

    Note: for roots, the SKI should be the same value as the AKI, therefore,
    this extension isn't strictly necessary, although required by the RFC.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.2
    """
    root = builder.root_ca(ski=None)
    leaf = builder.leaf_cert(root)

    builder = builder.server_validation()
    builder = builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def multiple_chains_expired_intermediate(builder: Builder) -> None:
    """
    Produces the following chain:

    ```
    root 2 -> intermediate (expired) -> root -> EE
    ```

    Both roots are trusted. A chain should be built successfully, disregarding
    the expired intermediate certificate and the second root. This scenario is
    known as the "chain of pain"; for further reference, see
    https://www.agwa.name/blog/post/fixing_the_addtrust_root_expiration.
    """
    root = builder.root_ca()
    root_two = builder.root_ca(issuer=x509.Name.from_rfc4514_string("CN=x509-limbo-root-2"))
    ski = x509.SubjectKeyIdentifier.from_public_key(root.key.public_key())  # type: ignore[arg-type]
    expired_intermediate = builder.intermediate_ca(
        root_two,
        pathlen=1,
        subject=root.cert.subject,
        not_after=datetime.fromisoformat("1988-11-25T00:00:00Z"),
        key=root.key,
        ski=ext(ski, critical=False),
    )
    leaf = builder.leaf_cert(root)

    builder = builder.server_validation()
    builder.trusted_certs(root, root_two).untrusted_intermediates(
        expired_intermediate
    ).peer_certificate(leaf).succeeds()


@testcase
def chain_untrusted_root(builder: Builder) -> None:
    """
    Produces the following chain:

    ```
    root (untrusted) -> intermediate -> EE
    ```

    The root is not in the trusted set, thus no chain should be built.
    Verification can't be achieved without trusted certificates so we add an
    unrelated root CA to create a more realistic scenario.
    """
    root = builder.root_ca()
    intermediate = builder.intermediate_ca(root, pathlen=0)
    leaf = builder.leaf_cert(intermediate)
    unrelated_root = builder.root_ca(
        issuer=x509.Name.from_rfc4514_string("CN=x509-limbo-unrelated-root")
    )

    builder = builder.server_validation()
    builder.trusted_certs(unrelated_root).untrusted_intermediates(
        root, intermediate
    ).peer_certificate(leaf).fails()


@testcase
def intermediate_ca_without_ca_bit(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> intermediate -> EE
    ```

    The intermediate CA does not have the cA bit set in BasicConstraints, thus
    no valid chain to the leaf exists per the [RFC 5280 profile]:

    > If the basic constraints extension is not present in a version 3
    > certificate, or the extension is present but the cA boolean
    > is not asserted, then the certified public key MUST NOT be used to
    > verify certificate signatures.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.9
    """
    root = builder.root_ca()
    intermediate = builder.intermediate_ca(
        root,
        basic_constraints=ext(x509.BasicConstraints(False, path_length=None), critical=True),
    )
    leaf = builder.leaf_cert(intermediate)

    builder = builder.server_validation()
    builder.trusted_certs(root).untrusted_intermediates(intermediate).peer_certificate(leaf).fails()


@testcase
def intermediate_ca_missing_basic_constraints(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> ICA -> EE
    ```

    The intermediate CA is missing the BasicConstraints extension, which is disallowed
    under the [RFC 5280 profile]:

    > Conforming CAs MUST include this extension in all CA certificates
    > that contain public keys used to validate digital signatures on
    > certificates and MUST mark the extension as critical in such
    > certificates.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.9
    """
    root = builder.root_ca()
    intermediate = builder.intermediate_ca(root, basic_constraints=None)
    leaf = builder.leaf_cert(intermediate)

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def root_missing_basic_constraints(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> EE
    ```

    The root CA is missing the BasicConstraints extension, which is disallowed
    under the [RFC 5280 profile]:

    > Conforming CAs MUST include this extension in all CA certificates
    > that contain public keys used to validate digital signatures on
    > certificates and MUST mark the extension as critical in such
    > certificates.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.9
    """
    root = builder.root_ca(basic_constraints=None)
    leaf = builder.leaf_cert(root)

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def root_non_critical_basic_constraints(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> EE
    ```

    The root CA has a non-critical BasicConstraints extension, which is disallowed
    under the [RFC 5280 profile]:

    > Conforming CAs MUST include this extension in all CA certificates
    > that contain public keys used to validate digital signatures on
    > certificates and MUST mark the extension as critical in such
    > certificates.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.9
    """
    root = builder.root_ca(basic_constraints=ext(x509.BasicConstraints(True, None), critical=False))
    leaf = builder.leaf_cert(root)

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def root_inconsistent_ca_extensions(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> EE
    ```

    The root CA has BasicConstraints.cA=TRUE and KeyUsage.keyCertSign=FALSE.
    According to the [RFC 5280 profile], these two fields are related in the
    following ways:

    > If the keyCertSign bit is asserted, then the cA bit in the basic
    > constraints extension MUST also be asserted. (Section 4.2.1.3)

    and

    > If the cA boolean is not asserted, then the keyCertSign bit in the
    > key usage extension MUST NOT be asserted. (Section 4.2.1.9)

    Although the profile does not directly state that keyCertSign must be asserted
    when cA is asserted, this configuration is inconsistent and clients should
    reject it.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280
    """
    root = builder.root_ca(
        key_usage=ext(
            x509.KeyUsage(
                digital_signature=False,
                key_cert_sign=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=False,
        ),
    )
    leaf = builder.leaf_cert(root)

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def ica_ku_keycertsign(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> ICA -> EE
    ```

    The intermediate CA includes BasicConstraints with pathLenConstraint=0 and
    KeyUsage.keyCertSign=FALSE, which is disallowed under the [RFC 5280 profile]:

    > CAs MUST NOT include the pathLenConstraint field unless the cA
    > boolean is asserted and the key usage extension asserts the
    > keyCertSign bit.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.9
    """
    root = builder.root_ca()
    intermediate = builder.intermediate_ca(
        root,
        pathlen=0,
        key_usage=ext(
            x509.KeyUsage(
                digital_signature=False,
                key_cert_sign=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=False,
        ),
    )
    leaf = builder.leaf_cert(intermediate)

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def leaf_ku_keycertsign(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> EE
    ```

    The leaf has a BasicConstraints extension with cA=FALSE and a KeyUsage
    extension with keyCertSign=TRUE. This is disallowed under the
    [RFC 5280 profile]:

    > The cA boolean indicates whether the certified public key may be used
    > to verify certificate signatures.  If the cA boolean is not asserted,
    > then the keyCertSign bit in the key usage extension MUST NOT be
    > asserted.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.9
    """
    root = builder.root_ca()
    leaf = builder.leaf_cert(
        root,
        basic_constraints=ext(x509.BasicConstraints(False, None), critical=True),
        key_usage=ext(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=False,
        ),
    )

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def ca_nameconstraints_permitted_dns_mismatch(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with a permitted dNSName
    "example.com", whereas the leaf certificate has a SubjectAlternativeName with a
    dNSName of "not-example.com".
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=[x509.DNSName("example.com")], excluded_subtrees=None
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root,
        san=ext(x509.SubjectAlternativeName([x509.DNSName("not-example.com")]), critical=False),
    )

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def ca_nameconstraints_excluded_dns_match(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with an excluded dNSName of
    "example.com", matching the leaf's SubjectAlternativeName.
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=None, excluded_subtrees=[x509.DNSName("example.com")]
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root, san=ext(x509.SubjectAlternativeName([x509.DNSName("example.com")]), critical=False)
    )

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="DNS", value="example.com")
    ).fails()


@testcase
def ca_nameconstraints_permitted_dns_match(builder: Builder) -> None:
    """
    Produces the following **valid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with a permitted dNSName of
    "example.com", matching the leaf's SubjectAlternativeName.
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=[x509.DNSName("example.com")], excluded_subtrees=None
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root, san=ext(x509.SubjectAlternativeName([x509.DNSName("example.com")]), critical=False)
    )

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="DNS", value="example.com")
    ).succeeds()


@testcase
def ca_nameconstraints_permitted_dns_match_more(builder: Builder) -> None:
    """
    Produces the following **valid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with a permitted dNSName of
    "example.com". The leaf's "foo.bar.example.com" satisfies this constraint
    per the [RFC 5280 profile]:

    > DNS name restrictions are expressed as host.example.com.  Any DNS
    > name that can be constructed by simply adding zero or more labels to
    > the left-hand side of the name satisfies the name constraint.  For
    > example, www.host.example.com would satisfy the constraint but
    > host1.example.com would not.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.10
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=[x509.DNSName("example.com")], excluded_subtrees=None
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root,
        san=ext(x509.SubjectAlternativeName([x509.DNSName("foo.bar.example.com")]), critical=False),
    )

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="DNS", value="foo.bar.example.com")
    ).succeeds()


@testcase
def ca_nameconstraints_excluded_dns_match_second(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with an excluded dNSName of
    "not-allowed.example.com". This should match the leaf's second
    SubjectAlternativeName entry.
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=None, excluded_subtrees=[x509.DNSName("not-allowed.example.com")]
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root,
        san=ext(
            x509.SubjectAlternativeName(
                [x509.DNSName("example.com"), x509.DNSName("not-allowed.example.com")]
            ),
            critical=False,
        ),
    )

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="DNS", value="example.com")
    ).fails()


@testcase
def ca_nameconstraints_permitted_ip_mismatch(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with a permitted iPAddress of
    192.0.2.0/24, which does not match the iPAddress in the SubjectAlternativeName
    of the leaf.
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=[x509.IPAddress(IPv4Network("192.0.2.0/24"))],
                excluded_subtrees=None,
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root,
        san=ext(
            x509.SubjectAlternativeName([x509.IPAddress(IPv4Address("192.0.3.1"))]), critical=False
        ),
    )

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="IP", value="192.0.3.1")
    ).fails()


@testcase
def ca_nameconstraints_excluded_ip_match(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with an excluded iPAddress of
    192.0.2.0/24, matching the iPAddress in the SubjectAlternativeName of the leaf.
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=None,
                excluded_subtrees=[x509.IPAddress(IPv4Network("192.0.2.0/24"))],
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root,
        san=ext(
            x509.SubjectAlternativeName([x509.IPAddress(IPv4Address("192.0.2.1"))]), critical=False
        ),
    )

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="IP", value="192.0.2.1")
    ).fails()


@testcase
def ca_nameconstraints_permitted_ip_match(builder: Builder) -> None:
    """
    Produces the following **valid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with a permitted iPAddress of
    192.0.2.0/24, which matches the iPAddress in the SubjectAlternativeName
    of the leaf.
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=[x509.IPAddress(IPv4Network("192.0.2.0/24"))],
                excluded_subtrees=None,
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root,
        san=ext(
            x509.SubjectAlternativeName([x509.IPAddress(IPv4Address("192.0.2.1"))]), critical=False
        ),
    )

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="IP", value="192.0.2.1")
    ).succeeds()


@testcase
def ca_nameconstraints_permitted_dn_mismatch(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with a permitted DirectoryName
    of "CN=foo". This should not match the child's DirectoryName of "CN=not-foo".
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=[x509.DirectoryName(x509.Name.from_rfc4514_string("CN=foo"))],
                excluded_subtrees=None,
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root,
        subject=x509.Name.from_rfc4514_string("CN=not-foo"),
        san=ext(
            x509.SubjectAlternativeName(
                [x509.DirectoryName(x509.Name.from_rfc4514_string("CN=not-foo"))]
            ),
            critical=False,
        ),
    )

    builder = builder.server_validation().features([Feature.name_constraint_dn])
    builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def ca_nameconstraints_excluded_dn_match(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with an excluded DirectoryName
    of "CN=foo", matching the leaf's SubjectAlternativeName.
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=None,
                excluded_subtrees=[x509.DirectoryName(x509.Name.from_rfc4514_string("CN=foo"))],
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root,
        subject=x509.Name.from_rfc4514_string("CN=foo"),
        san=ext(
            x509.SubjectAlternativeName(
                [x509.DirectoryName(x509.Name.from_rfc4514_string("CN=foo"))]
            ),
            critical=False,
        ),
    )

    builder = builder.server_validation().features([Feature.name_constraint_dn])
    builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def ca_nameconstraints_permitted_dn_match(builder: Builder) -> None:
    """
    Produces the following **valid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with a permitted DirectoryName
    of "CN=foo", matching the leaf's SubjectAlternativeName.
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=[x509.DirectoryName(x509.Name.from_rfc4514_string("CN=foo"))],
                excluded_subtrees=None,
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root,
        subject=x509.Name.from_rfc4514_string("CN=foo"),
        san=ext(
            x509.SubjectAlternativeName(
                [x509.DirectoryName(x509.Name.from_rfc4514_string("CN=foo"))]
            ),
            critical=False,
        ),
    )

    builder = builder.server_validation().features([Feature.name_constraint_dn])
    builder.trusted_certs(root).peer_certificate(leaf).succeeds()


@testcase
def ca_nameconstraints_permitted_dn_match_subject_san_mismatch(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with a permitted DirectoryName
    of "CN=foo", matching the leaf's SubjectAlternativeName but not its subject.
    The leaf must be rejected per the [RFC5280 profile] due to this mismatch:

    > Restrictions of the form directoryName MUST be applied to the subject
    > field in the certificate (when the certificate includes a non-empty
    > subject field) and to any names of type directoryName in the
    > subjectAltName extension.

    [RFC5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.10
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=[x509.DirectoryName(x509.Name.from_rfc4514_string("CN=foo"))],
                excluded_subtrees=None,
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root,
        subject=x509.Name.from_rfc4514_string("CN=not-foo"),
        san=ext(
            x509.SubjectAlternativeName(
                [x509.DirectoryName(x509.Name.from_rfc4514_string("CN=foo"))]
            ),
            critical=False,
        ),
    )

    builder = builder.server_validation().features([Feature.name_constraint_dn])
    builder.trusted_certs(root).peer_certificate(leaf).fails()


@testcase
def ca_nameconstraints_excluded_dn_match_sub_mismatch(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with an excluded DirectoryName
    of "CN=foo", matching the leaf's subject but not its SubjectAlternativeName.
    The leaf must be rejected per the [RFC5280 profile] due to this match:

    > Restrictions of the form directoryName MUST be applied to the subject
    > field in the certificate (when the certificate includes a non-empty
    > subject field) and to any names of type directoryName in the
    > subjectAltName extension.

    [RFC5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2.1.10
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=None,
                excluded_subtrees=[x509.DirectoryName(x509.Name.from_rfc4514_string("CN=foo"))],
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root,
        subject=x509.Name.from_rfc4514_string("CN=foo"),
        san=ext(
            x509.SubjectAlternativeName(
                [x509.DirectoryName(x509.Name.from_rfc4514_string("CN=not-foo"))]
            ),
            critical=False,
        ),
    )

    builder = builder.server_validation().features([Feature.name_constraint_dn])
    builder.trusted_certs(root).peer_certificate(leaf).fails()


# NOTE: The following tests aren't specific to any name constraint type.
# We could potentially parametrize this for different constraint types.
@testcase
def ca_nameconstraints_permitted_self_issued(builder: Builder) -> None:
    """
    Produces the following **valid** chain:

    ```
    root -> intermediate -> leaf
    ```

    The root contains a NameConstraints extension with a permitted dNSName of
    "example.com", whereas the intermediate certificate has a
    SubjectAlternativeName with a dNSName of "not-example.com".

    Normally, this would mean that the chain would be rejected, however the
    intermediate is self-issued so name constraints don't apply to it.

    > Name constraints are not applied to self-issued certificates (unless
    > the certificate is the final certificate in the path).  (This could
    > prevent CAs that use name constraints from employing self-issued
    > certificates to implement key rollover.)
    """
    root = builder.root_ca(
        issuer=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "not-example.com")]),
        subject=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "not-example.com")]),
        san=ext(x509.SubjectAlternativeName([x509.DNSName("not-example.com")]), critical=False),
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=[x509.DNSName("example.com")],
                excluded_subtrees=None,
            ),
            critical=True,
        ),
    )
    intermediate = builder.intermediate_ca(
        root,
        issuer=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "not-example.com")]),
        subject=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "not-example.com")]),
        san=ext(x509.SubjectAlternativeName([x509.DNSName("not-example.com")]), critical=False),
    )
    leaf = builder.leaf_cert(intermediate)
    builder = builder.server_validation()
    builder.trusted_certs(root).untrusted_intermediates(intermediate).peer_certificate(
        leaf
    ).succeeds()


@testcase
def ca_nameconstraints_excluded_self_issued_leaf(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> intermediate -> leaf
    ```

    The root contains a NameConstraints extension with a permitted dNSName of
    "example.com", whereas the leaf certificate has a SubjectAlternativeName
    with a dNSName of "not-example.com".

    In this case, the chain would still be rejected as name constraints do apply
    to self-issued certificates if they are in the leaf position.

    > Name constraints are not applied to self-issued certificates (unless
    > the certificate is the final certificate in the path).  (This could
    > prevent CAs that use name constraints from employing self-issued
    > certificates to implement key rollover.)
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=[x509.DNSName("example.com")],
                excluded_subtrees=None,
            ),
            critical=True,
        )
    )
    intermediate = builder.intermediate_ca(
        root,
        subject=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "not-example.com")]),
        san=ext(x509.SubjectAlternativeName([x509.DNSName("not-example.com")]), critical=False),
    )
    leaf = builder.leaf_cert(
        intermediate,
        issuer=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "not-example.com")]),
        subject=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "not-example.com")]),
        san=ext(x509.SubjectAlternativeName([x509.DNSName("not-example.com")]), critical=False),
    )
    builder = builder.server_validation()
    builder.trusted_certs(root).untrusted_intermediates(intermediate).peer_certificate(
        leaf
    ).expected_peer_name(PeerName(kind="DNS", value="not-example.com")).fails()


@testcase
def ca_nameconstraints_excluded_match_permitted_and_excluded(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with a permitted and excluded
    dNSName of "example.com", both of which match the leaf's
    SubjectAlternativeName.

    The excluded constraint takes precedence over the the permitted so this
    chain should be marked as invalid.
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=[x509.DNSName("example.com")],
                excluded_subtrees=[x509.DNSName("example.com")],
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root,
        san=ext(x509.SubjectAlternativeName([x509.DNSName("example.com")]), critical=False),
    )
    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="DNS", value="example.com")
    ).fails()


@testcase
def ca_nameconstraints_permitted_different_constraint_type(builder: Builder) -> None:
    """
    Produces the following **valid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with a permitted iPAddress of
    192.0.2.0/24, while the leaf's SubjectAlternativeName is a dNSName.
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=[x509.IPAddress(IPv4Network("192.0.2.0/24"))],
                excluded_subtrees=None,
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root,
        san=ext(x509.SubjectAlternativeName([x509.DNSName("example.com")]), critical=False),
    )
    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="DNS", value="example.com")
    ).succeeds()


@testcase
def ca_nameconstraints_excluded_different_constraint_type(builder: Builder) -> None:
    """
    Produces the following **valid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with an excluded iPAddress of
    192.0.2.0/24, while the leaf's SubjectAlternativeName is a dNSName.
    """
    root = builder.root_ca(
        name_constraints=ext(
            x509.NameConstraints(
                permitted_subtrees=None,
                excluded_subtrees=[x509.IPAddress(IPv4Network("192.0.2.0/24"))],
            ),
            critical=True,
        )
    )
    leaf = builder.leaf_cert(
        root,
        san=ext(x509.SubjectAlternativeName([x509.DNSName("example.com")]), critical=False),
    )
    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="DNS", value="example.com")
    ).succeeds()


@testcase
def ca_nameconstraints_invalid_dnsname(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with a malformed dNSName
    (uses a wildcard pattern, which is not permitted under RFC 5280).
    """

    # NOTE: Set `_permitted_subtrees` directly to avoid validation.
    name_constraints = x509.NameConstraints(
        permitted_subtrees=[x509.DNSName("unrelated.cryptography.io")], excluded_subtrees=None
    )
    name_constraints._permitted_subtrees = [x509.DNSName("*.example.com")]

    root = builder.root_ca(name_constraints=ext(name_constraints, critical=True))
    leaf = builder.leaf_cert(
        root,
        san=ext(x509.SubjectAlternativeName([x509.DNSName("foo.example.com")]), critical=False),
    )

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="DNS", value="foo.example.com")
    ).fails()


@testcase
def ca_nameconstraints_invalid_ipaddress(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> leaf
    ```

    The root contains a NameConstraints extension with a malformed iPAddress
    (not in CIDR form).
    """

    # NOTE: Set `_permitted_subtrees` directly to avoid validation.
    name_constraints = x509.NameConstraints(
        permitted_subtrees=[x509.IPAddress(IPv4Network("0.0.0.0/8"))], excluded_subtrees=None
    )
    name_constraints._permitted_subtrees = [x509.IPAddress(IPv4Address("127.0.0.1"))]

    root = builder.root_ca(name_constraints=ext(name_constraints, critical=True))
    leaf = builder.leaf_cert(
        root,
        san=ext(
            x509.SubjectAlternativeName([x509.IPAddress(IPv4Address("127.0.0.1"))]), critical=False
        ),
    )

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="IP", value="127.0.0.1")
    ).fails()


@testcase
def ee_aia(builder: Builder) -> None:
    """
    Produces a **valid** chain with an EE cert.

    This EE cert contains an Authority Information Access extension with a CA Issuer Access
    Description.
    """
    root = builder.root_ca()
    leaf = builder.leaf_cert(
        root,
        extra_extension=ext(
            x509.AuthorityInformationAccess(
                [x509.AccessDescription(x509.OID_CA_ISSUERS, x509.DNSName("example.com"))]
            ),
            critical=False,
        ),
    )

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="DNS", value="example.com")
    ).succeeds()


@testcase
def ee_critical_aia_invalid(builder: Builder) -> None:
    """
    Produces a **invalid** chain with an EE cert.

    This EE cert contains an Authority Information Access extension with a CA Issuer Access
    Description. The AIA extension is marked as critical, which is disallowed
    under RFC 5280:

    > Conforming CAs MUST mark this extension as non-critical.
    """
    root = builder.root_ca()
    leaf = builder.leaf_cert(
        root,
        extra_extension=ext(
            x509.AuthorityInformationAccess(
                [x509.AccessDescription(x509.OID_CA_ISSUERS, x509.DNSName("example.com"))]
            ),
            critical=True,
        ),
    )

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="DNS", value="example.com")
    ).fails()


@testcase
def san_noncritical_with_empty_subject(builder: Builder) -> None:
    """
    Produces an **invalid** chain due to an invalid EE cert.

    The EE cert contains a non-critical Subject Alternative Name extension,
    which is disallowed when the cert's Subject is empty under
    RFC 5280:

    > If the subject field contains an empty sequence, then the issuing CA MUST
    > include a subjectAltName extension that is marked as critical.
    """

    root = builder.root_ca()
    leaf = builder.leaf_cert(
        root,
        subject=x509.Name([]),
        san=ext(x509.SubjectAlternativeName([x509.DNSName("example.com")]), critical=False),
    )

    builder = builder.server_validation()
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="DNS", value="example.com")
    ).fails()


@testcase
def serial_number_too_long(builder: Builder) -> None:
    """
    Produces an **invalid** chain due to an invalid EE cert.

    The EE cert contains a serial number longer than 20 octets, which is
    disallowed under RFC 5280.
    """

    root = builder.root_ca()
    # NOTE: Intentionally generate 22 octets, since many implementations are
    # permissive of 21-octet encodings due to signedness errors.
    leaf = builder.leaf_cert(root, serial=int.from_bytes(random.randbytes(22), signed=False))

    builder = builder.server_validation().features([Feature.pedantic_serial_number])
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="DNS", value="example.com")
    ).fails()


@testcase
def serial_number_zero(builder: Builder) -> None:
    """
    Produces an **invalid** chain due to an invalid EE cert.

    The EE cert contains a serial number of zero, which is disallowed
    under RFC 5280.
    """

    root = builder.root_ca()
    leaf = builder.leaf_cert(root, serial=0)

    builder = builder.server_validation().features([Feature.pedantic_serial_number])
    builder.trusted_certs(root).peer_certificate(leaf).expected_peer_name(
        PeerName(kind="DNS", value="example.com")
    ).fails()


@testcase
def duplicate_extensions(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> EE
    ```

    This chain is invalid solely because of the EE cert's construction:
    it contains multiple X.509v3 extensions with the same OID, which
    is prohibited under the [RFC 5280 profile].

    > A certificate MUST NOT include more than one instance of a particular
    > extension.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2
    """

    root = builder.root_ca()
    leaf = builder.leaf_cert(
        root,
        san=None,
        extra_unchecked_extensions=[
            ext(x509.SubjectAlternativeName([x509.DNSName("example.com")]), critical=False),
            ext(x509.SubjectAlternativeName([x509.DNSName("example.com")]), critical=False),
        ],
    )

    builder = builder.server_validation()
    builder = (
        builder.trusted_certs(root)
        .peer_certificate(leaf)
        .expected_peer_name(PeerName(kind="DNS", value="example.com"))
        .fails()
    )


@testcase
def no_keyusage(builder: Builder) -> None:
    """
    Produces the following **valid** chain:

    ```
    root -> EE
    ```

    The EE lacks a Key Usage extension, which is not required for
    end-entity certificates under the RFC 5280 profile.
    """

    root = builder.root_ca()
    leaf = builder.leaf_cert(root, key_usage=None)

    builder = builder.server_validation()
    builder = (
        builder.trusted_certs(root)
        .peer_certificate(leaf)
        .expected_peer_name(PeerName(kind="DNS", value="example.com"))
        .succeeds()
    )


@testcase
def no_basicconstraints(builder: Builder) -> None:
    """
    Produces the following **valid** chain:

    ```
    root -> EE
    ```

    The EE lacks a Basic Constraints extension, which is not required for
    end-entity certificates under the RFC 5280 profile.
    """
    root = builder.root_ca()
    leaf = builder.leaf_cert(root, basic_constraints=None)

    builder = builder.server_validation()
    builder = (
        builder.trusted_certs(root)
        .peer_certificate(leaf)
        .expected_peer_name(PeerName(kind="DNS", value="example.com"))
        .succeeds()
    )


@testcase
def wrong_eku(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> EE
    ```

    The chain is correctly constructed, but the EE cert contains
    an Extended Key Usage extension that contains just `id-kp-clientAuth`
    while the validator expects `id-kp-serverAuth`.
    """

    root = builder.root_ca()
    leaf = builder.leaf_cert(
        root,
        eku=ext(
            x509.ExtendedKeyUsage([x509.OID_CLIENT_AUTH]),
            critical=False,
        ),
    )

    builder = builder.server_validation()
    builder = (
        builder.trusted_certs(root)
        .extended_key_usage([KnownEKUs.server_auth])
        .peer_certificate(leaf)
        .expected_peer_name(PeerName(kind="DNS", value="example.com"))
        .fails()
    )


@testcase
def mismatching_signature_algorithm(builder: Builder) -> None:
    """
    Verifies against a saved copy of `cryptography.io`'s chain with
    the root certificate modified to have mismatched `signatureAlgorithm`
    fields, which is prohibited under the [RFC 5280 profile].

    > A certificate MUST NOT include more than one instance of a particular
    > extension.

    [RFC 5280 profile]: https://datatracker.ietf.org/doc/html/rfc5280#section-4.2
    """
    chain_path = _ASSETS_PATH / "cryptography.io_mismatched.pem"
    chain = [Certificate(c) for c in x509.load_pem_x509_certificates(chain_path.read_bytes())]

    leaf, root = chain.pop(0), chain.pop(-1)
    builder = builder.server_validation().validation_time(
        datetime.fromisoformat("2023-07-10T00:00:00Z")
    )
    builder = (
        builder.trusted_certs(root)
        .peer_certificate(leaf)
        .untrusted_intermediates(*chain)
        .expected_peer_name(PeerName(kind="DNS", value="cryptography.io"))
    ).fails()


@testcase
def malformed_subject_alternative_name(builder: Builder) -> None:
    """
    Produces the following **invalid** chain:

    ```
    root -> EE
    ```

    The EE cert has a SubjectAlternativeName with a value in ASCII bytes, rather
    than in the expected DER encoding.
    """
    root = builder.root_ca()
    malformed_san = ext(
        x509.UnrecognizedExtension(x509.OID_SUBJECT_ALTERNATIVE_NAME, b"example.com"),
        critical=False,
    )
    leaf = builder.leaf_cert(root, san=None, extra_extension=malformed_san)

    builder = builder.server_validation()
    builder = builder.trusted_certs(root).peer_certificate(leaf).fails()
