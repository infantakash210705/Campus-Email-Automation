import pytest

from shortfundly import validation as v


@pytest.mark.parametrize("email", [
    "a@demo-college.example", "first.last@sub.demo-college.example", "a+tag@demo.example",
])
def test_valid_emails(email):
    assert v.validate_email(email)


@pytest.mark.parametrize("email", [
    "", None, "plain", "a@", "@demo.example", "a@demo", "a b@demo.example", "a..b@demo.example",
    ".a@demo.example", "a.@demo.example", "a@-demo.example", "a@demo..example", "a@@demo.example",
    "a@demo.example\nBcc: victim@demo.example",
])
def test_invalid_emails(email):
    assert not v.validate_email(email)


def test_required_fields_reported():
    errors = v.validate_contact({"state": "", "city": " ", "college_name": "X", "email": "x@y.example"})
    assert any("State" in e for e in errors)
    assert any("City" in e for e in errors)
    assert not any("College name" in e for e in errors)


def test_invalid_email_and_urls_reported():
    errors = v.validate_contact({
        "state": "S", "city": "C", "college_name": "N", "email": "nope",
        "website": "javascript:alert(1)", "source_url": "https://ok.example/page",
    })
    assert any("Invalid email" in e for e in errors)
    assert any("Official website" in e for e in errors)
    assert not any("Source page" in e for e in errors)


def test_clean_record_normalises():
    rec = v.clean_record({"state": "  Sampleland ", "city": "A  B", "college_name": "N",
                          "email": " Mixed@Demo.Example ", "website": "www.demo.example"})
    assert rec["state"] == "Sampleland" and rec["city"] == "A B"
    assert rec["email"] == "mixed@demo.example"
    assert rec["website"] == "https://www.demo.example"


def test_college_key_ignores_case_and_punctuation():
    assert v.college_key("The  Demo College!", "Exampleville") == v.college_key("the demo college", "EXAMPLEVILLE")
    assert v.college_key("Demo College", "A") != v.college_key("Demo College", "B")


def test_email_matches_website():
    assert v.email_matches_website("a@demo.example", "https://www.demo.example/")
    assert v.email_matches_website("a@cs.demo.example", "https://demo.example")
    assert not v.email_matches_website("someone@gmail.com", "https://gmail.com")
    assert not v.email_matches_website("a@other.example", "https://demo.example")
    # a shared public suffix must not count as the same organisation
    assert not v.email_matches_website("a@edu.in", "https://college.edu.in")
