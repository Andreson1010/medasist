from __future__ import annotations

from pathlib import Path

from medasist.policies.rules import check_patient_data

_ROOT = Path(".")

# Amostras montadas em tempo de execução para não literalizar padrões de
# paciente no fonte (o próprio scanner valida tests/ com PATIENT-DATA).


def _cpf() -> str:
    return "123" + ".456" + ".789-00"


def _rg() -> str:
    return "12.345" + ".678-9"


def _rg_letter() -> str:
    return "12.345.678" + "-X"


def _rg_compact_digit() -> str:
    return "12345678" + "-9"


def _rg_compact_letter() -> str:
    return "12345678" + "X"


def _sus() -> str:
    return "12345678" + "9012345"


def _phone() -> str:
    return "(11) 9123" + "4-5678"


def _mobile() -> str:
    return "119" + "12345678"


def _email() -> str:
    return "paciente@" + "exemplo.com"


def _dob() -> str:
    return "15/03/19" + "85"


class TestPatientDataPositives:
    def test_cpf_is_violation(self) -> None:
        violations = check_patient_data(
            f'cpf = "{_cpf()}"\n', "tests/fixture.py", _ROOT
        )
        assert len(violations) == 1
        assert violations[0].rule_id == "PATIENT-DATA"
        assert "CPF" in violations[0].message

    def test_cpf_without_dots_is_violation(self) -> None:
        violations = check_patient_data(
            f'cpf = "12345678{_cpf()[-2:]}"\n', "tests/fixture.py", _ROOT
        )
        assert len(violations) == 1

    def test_rg_with_dots_is_violation(self) -> None:
        violations = check_patient_data(f'rg = "{_rg()}"\n', "tests/fixture.py", _ROOT)
        assert len(violations) == 1
        assert "RG" in violations[0].message

    def test_rg_with_letter_check_digit_is_violation(self) -> None:
        violations = check_patient_data(
            f'rg = "{_rg_letter()}"\n', "tests/fixture.py", _ROOT
        )
        assert len(violations) == 1

    def test_rg_compact_with_digit_check_digit_is_violation(self) -> None:
        violations = check_patient_data(
            f'rg = "{_rg_compact_digit()}"\n', "tests/fixture.py", _ROOT
        )
        assert len(violations) == 1
        assert "RG" in violations[0].message

    def test_rg_compact_with_letter_check_digit_is_violation(self) -> None:
        violations = check_patient_data(
            f'rg = "{_rg_compact_letter()}"\n', "tests/fixture.py", _ROOT
        )
        assert len(violations) == 1

    def test_sus_card_is_violation(self) -> None:
        violations = check_patient_data(
            f'sus = "{_sus()}"\n', "tests/fixture.py", _ROOT
        )
        assert len(violations) == 1
        assert "SUS" in violations[0].message

    def test_phone_with_ddd_is_violation(self) -> None:
        violations = check_patient_data(
            f'telefone = "{_phone()}"\n', "tests/fixture.py", _ROOT
        )
        assert len(violations) == 1
        assert "TELEFONE" in violations[0].message

    def test_mobile_without_parens_is_violation(self) -> None:
        violations = check_patient_data(
            f'telefone = "{_mobile()}"\n', "tests/fixture.py", _ROOT
        )
        assert len(violations) == 1

    def test_email_is_violation(self) -> None:
        violations = check_patient_data(
            f'email = "{_email()}"\n', "tests/fixture.py", _ROOT
        )
        assert len(violations) == 1
        assert "EMAIL" in violations[0].message

    def test_dob_in_age_range_is_violation(self) -> None:
        violations = check_patient_data(
            f'nascimento = "{_dob()}"\n', "tests/fixture.py", _ROOT
        )
        assert len(violations) == 1
        assert "DATA-NASCIMENTO" in violations[0].message


class TestPatientDataNegatives:
    def test_synthetic_drug_names_are_legal(self) -> None:
        text = (
            "paciente usa Zolatril 500mg\n"
            "Alphazol e Betazol em gotas\n"
            "amoxicilina 500mg, ibuprofeno, omeprazol\n"
            "Gammacol e dipirona e paracetamol\n"
        )
        assert check_patient_data(text, "tests/fixture.py", _ROOT) == []

    def test_dob_outside_age_range_is_legal(self) -> None:
        assert (
            check_patient_data('data = "01/01/1900"\n', "tests/fixture.py", _ROOT) == []
        )

    def test_phone_starting_with_zero_is_legal(self) -> None:
        # DDD 01 inválido no Brasil: não casa como telefone
        assert (
            check_patient_data('codigo = "0123456789"\n', "tests/fixture.py", _ROOT)
            == []
        )

    def test_embedded_numbers_are_not_matched(self) -> None:
        assert (
            check_patient_data(
                'hash = "abc12345678900def"\n', "tests/fixture.py", _ROOT
            )
            == []
        )

    def test_rg_embedded_in_longer_token_is_legal(self) -> None:
        assert (
            check_patient_data('hash = "abc123456789def"\n', "tests/fixture.py", _ROOT)
            == []
        )

    def test_short_numeric_sequence_is_legal(self) -> None:
        assert (
            check_patient_data('codigo = "12345678"\n', "tests/fixture.py", _ROOT) == []
        )

    def test_plain_text_is_legal(self) -> None:
        assert (
            check_patient_data('nome = "Maria da Silva"\n', "tests/fixture.py", _ROOT)
            == []
        )

    def test_line_with_allowlist_token_suppresses_match(self) -> None:
        text = f"Zolatril: {_cpf()}\n"
        assert check_patient_data(text, "tests/fixture.py", _ROOT) == []

    def test_extra_allowlist_parameter(self) -> None:
        text = f"fixture sintetica: {_cpf()}\n"
        violations = check_patient_data(
            text,
            "tests/fixture.py",
            _ROOT,
            allowlist=frozenset({"fixture sintetica"}),
        )
        assert violations == []

    def test_crlf_lines_are_handled(self) -> None:
        text = f'cpf = "{_cpf()}"\r\n'
        violations = check_patient_data(text, "tests/fixture.py", _ROOT)
        assert len(violations) == 1
        assert violations[0].line == 1

    def test_reports_line_number(self) -> None:
        text = f'a = 1\nb = 2\ncpf = "{_cpf()}"\n'
        violations = check_patient_data(text, "tests/fixture.py", _ROOT)
        assert violations[0].line == 3
