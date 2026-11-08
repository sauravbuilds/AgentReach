"""Domain glossary: NRI / India finance terms.

Short acronyms are ambiguous ("RNOR" also matches "NOR", "OR", Telenor, a game character called Noor, ...).
For every term we know we do two things:
  1. add context words to the search so the search engine looks for the right topic, and
  2. keep only results that really mention the term (and, for terms that clash with other things,
     also mention an India / tax context word).
Definitions avoid rates and limits, which change; they only say what a term means.
"""
import re
from dataclasses import dataclass, field

_INDIA = (r"\bindia\b", r"\bindian\b", r"\bnri\b", r"\brbi\b", r"\brupee", r"\binr\b", "₹")


def _rx(*patterns: str) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


@dataclass(frozen=True)
class Entry:
    name: str
    terms: tuple[str, ...]  # what a user may type
    meaning: str  # plain explanation shown to the visitor
    also: str  # context words added to the search
    anchors: tuple[re.Pattern, ...] = ()  # a result must match one of these (empty: no check)
    require_any: tuple[re.Pattern, ...] = ()  # and one of these, for terms that clash with other topics
    pattern: re.Pattern = field(init=False, compare=False, repr=False, default=None)

    def __post_init__(self):
        alts = "|".join(re.escape(t) for t in sorted(self.terms, key=len, reverse=True))
        object.__setattr__(self, "pattern", re.compile(rf"(?<![A-Za-z0-9])(?:{alts})(?![A-Za-z0-9])", re.IGNORECASE))


def _e(name, terms, meaning, also, anchors=(), require_any=()):
    return Entry(name, tuple(terms), meaning, also, _rx(*anchors), _rx(*require_any))


ENTRIES: tuple[Entry, ...] = (
    _e("RNOR", ["RNOR", "resident but not ordinarily resident", "not ordinarily resident"],
       "RNOR (Resident but Not Ordinarily Resident) is an Indian income-tax status, usually for people returning to India "
       "after years abroad, under which income earned abroad is generally not taxed in India for a limited period.",
       "returning NRI India income tax residency status foreign income",
       anchors=(r"\bRNOR\b", r"not ordinarily resident")),
    _e("ROR", ["resident and ordinarily resident", "ROR status"],
       "ROR (Resident and Ordinarily Resident) is full Indian tax residency, where income from anywhere in the world is taxable in India.",
       "India income tax residential status global income",
       anchors=(r"\bordinarily resident\b",)),
    _e("NRI", ["NRI", "NRIs", "non-resident indian", "non resident indian"],
       "NRI (Non-Resident Indian) is an Indian citizen or person of Indian origin living abroad. The status decides India tax "
       "treatment and which bank accounts and investments they can use.",
       "India tax bank account investment from abroad",
       anchors=(r"\bNRIs?\b", r"non[- ]resident indian")),
    _e("OCI", ["OCI", "OCI card", "overseas citizen of india"],
       "OCI (Overseas Citizen of India) is a lifelong visa-like status for foreign citizens of Indian origin. The older PIO card was merged into it.",
       "India overseas citizen card foreign passport Indian origin",
       anchors=(r"\bOCI\b", r"overseas citizen of india"),
       require_any=(r"\bindia\b", r"\bindian\b", r"overseas citizen")),
    _e("PIO", ["PIO", "PIO card", "person of indian origin"],
       "PIO (Person of Indian Origin) was a card for people of Indian origin abroad. It was replaced by OCI in 2015.",
       "India person of Indian origin card OCI",
       anchors=(r"\bPIO\b", r"person of indian origin"),
       require_any=(r"\bindia\b", r"\bindian\b", r"\boci\b")),
    _e("NRE", ["NRE", "NRE account", "NRE fd"],
       "An NRE (Non-Resident External) account is a rupee bank account in India for money earned abroad. Interest is generally "
       "tax-free in India and the money can be moved back abroad.",
       "NRI bank account India rupee repatriation",
       anchors=(r"\bNRE\b", r"non[- ]resident external"),
       require_any=_INDIA),
    _e("NRO", ["NRO", "NRO account", "NRO fd"],
       "An NRO (Non-Resident Ordinary) account is a rupee account for income earned in India, such as rent or dividends. "
       "Interest is taxable, and moving money abroad has limits and paperwork.",
       "NRI bank account India rent income repatriation",
       anchors=(r"\bNRO\b", r"non[- ]resident ordinary"),
       require_any=_INDIA),
    _e("FCNR", ["FCNR", "FCNR deposit", "FCNR(B)"],
       "An FCNR (Foreign Currency Non-Resident) deposit is a fixed deposit in a foreign currency that NRIs can hold with Indian banks.",
       "NRI foreign currency fixed deposit India bank",
       anchors=(r"\bFCNR\b",)),
    _e("FEMA", ["FEMA"],
       "FEMA (Foreign Exchange Management Act) is the Indian law, enforced through RBI rules, on foreign currency, money transfers "
       "and what NRIs can hold or invest in.",
       "India RBI foreign exchange NRI rules",
       anchors=(r"\bFEMA\b",),
       require_any=_INDIA),
    _e("DTAA", ["DTAA", "double taxation avoidance agreement", "tax treaty india"],
       "A DTAA (Double Taxation Avoidance Agreement) is a tax treaty between India and another country that stops the same "
       "income being taxed twice and can lower the TDS rate.",
       "India tax treaty NRI TDS double taxation",
       anchors=(r"\bDTAA\b", r"double taxation avoidance", r"tax treaty")),
    _e("TRC", ["TRC", "tax residency certificate", "form 10F", "10F"],
       "A TRC (Tax Residency Certificate) from the foreign tax authority, together with Form 10F, is usually needed to claim "
       "treaty (DTAA) benefits in India.",
       "India NRI DTAA tax residency certificate form 10F",
       anchors=(r"\bTRC\b", r"tax residency certificate", r"\b10F\b"),
       require_any=_INDIA + (r"\bdtaa\b",)),
    _e("15CA/15CB", ["15CA", "15CB", "form 15CA", "form 15CB", "15CA/15CB", "15CA 15CB"],
       "Form 15CA (a declaration) and Form 15CB (a chartered accountant's certificate) are used to send money out of India "
       "when the payment is taxable, for example NRI property sale proceeds.",
       "NRI repatriation India remittance chartered accountant",
       anchors=(r"\b15CA\b", r"\b15CB\b")),
    _e("Section 195", ["section 195", "sec 195", "tds on nri", "tds for nri"],
       "Section 195 requires the payer to deduct tax at source (TDS) on certain payments to non-residents, for example when an NRI sells property.",
       "NRI TDS non-resident payment India tax deduction",
       anchors=(r"\bsection 195\b", r"\bsec\.? ?195\b", r"\bTDS\b"),
       require_any=_INDIA + (r"non[- ]resident",)),
    _e("NRI property sale", ["nri property sale", "nri selling property", "nri sell property", "selling property in india nri"],
       "When an NRI sells property in India the buyer deducts TDS, capital gains tax applies, a lower-deduction certificate "
       "can reduce the TDS, and moving the proceeds abroad needs paperwork.",
       "NRI sell property India TDS capital gains repatriation",
       anchors=(r"\bproperty\b", r"\bflat\b", r"\bplot\b", r"\bTDS\b", r"capital gains?"),
       require_any=_INDIA + (r"non[- ]resident", r"\boci\b")),
    _e("Lower deduction certificate", ["lower deduction certificate", "nil tds certificate", "lower tds certificate", "form 13"],
       "A lower-deduction certificate from the Income Tax Department allows a lower or nil TDS rate on a payment to a non-resident.",
       "India income tax certificate lower TDS NRI",
       anchors=(r"lower (?:tax )?deduction", r"\bform 13\b", r"nil tds", r"lower tds"),
       require_any=_INDIA + (r"\btds\b",)),
    _e("LRS", ["LRS", "liberalised remittance scheme", "liberalized remittance scheme"],
       "The LRS (Liberalised Remittance Scheme) is an RBI scheme that lets resident individuals send money abroad each year "
       "up to a limit, for investment, education, travel or gifts.",
       "India RBI remittance abroad resident individuals",
       anchors=(r"\bLRS\b", r"liberali[sz]ed remittance"),
       require_any=(r"\brbi\b", r"\bindia", r"remit", r"overseas", r"abroad", r"\btcs\b")),
    _e("TCS on remittance", ["tcs on remittance", "tcs on foreign remittance", "tcs on overseas remittance", "tcs lrs"],
       "TCS (Tax Collected at Source) is charged by banks on certain money sent abroad from India. It can be claimed as credit "
       "when filing the income tax return.",
       "India foreign remittance tax collected at source LRS credit",
       anchors=(r"\bTCS\b", r"tax collected at source"),
       require_any=(r"remit", r"abroad", r"overseas", r"\blrs\b")),
    _e("NRI ITR", ["nri itr", "itr for nri", "nri income tax return", "nri tax filing", "itr-2 nri"],
       "Filing an Indian income tax return as a non-resident: which form to use (usually ITR-2 or ITR-3), reporting Indian "
       "income and claiming TDS refunds.",
       "NRI income tax return India filing TDS refund",
       anchors=(r"\bITR\b", r"income tax return", r"tax return"),
       require_any=_INDIA + (r"non[- ]resident",)),
    _e("AIS / 26AS", ["AIS", "26AS", "form 26as", "annual information statement"],
       "AIS (Annual Information Statement) and Form 26AS are Income Tax Department statements of TDS, interest and other "
       "transactions, checked before filing a return.",
       "India income tax return TDS statement reconcile",
       anchors=(r"\bAIS\b", r"\b26AS\b", r"annual information statement"),
       require_any=(r"\btax", r"\bitr\b", r"\bincome\b")),
    _e("Deemed resident", ["deemed resident", "deemed to be resident", "15 lakh rule"],
       "The deemed-resident rule can treat an Indian citizen with high Indian income, who is not taxed anywhere else, as an "
       "Indian tax resident even when living abroad.",
       "India income tax NRI deemed resident citizen abroad",
       anchors=(r"deemed (?:to be )?resident",)),
    _e("Residency days", ["182 days", "182 day rule", "120 days india", "residential status india", "residential status"],
       "Rules that decide whether you are an NRI or an Indian tax resident, based on the days spent in India in the year "
       "and in earlier years.",
       "India NRI residential status days stayed income tax",
       anchors=(r"residential status", r"resident status", r"\b182\b", r"\b120 days\b"),
       require_any=_INDIA),
    _e("FATCA", ["FATCA"],
       "FATCA is a US law requiring foreign banks and funds to report accounts of US persons. It often comes up for NRIs with "
       "Indian bank accounts and mutual funds.",
       "NRI US India mutual fund KYC",
       anchors=(r"\bFATCA\b",),
       require_any=_INDIA + (r"mutual fund",)),
    _e("FBAR", ["FBAR", "fincen 114"],
       "FBAR is a US report of foreign bank accounts above a threshold. It matters for US taxpayers with Indian accounts.",
       "US taxpayer Indian bank account NRI foreign account report",
       anchors=(r"\bFBAR\b", r"fincen ?114"),
       require_any=_INDIA),
    _e("PFIC", ["PFIC"],
       "PFIC is a US tax rule that makes Indian mutual funds costly and complicated for US taxpayers.",
       "US taxpayer Indian mutual funds NRI",
       anchors=(r"\bPFIC\b",),
       require_any=_INDIA + (r"mutual fund",)),
    _e("Schedule FA", ["schedule fa", "foreign assets schedule"],
       "Schedule FA is the part of the Indian tax return where residents report foreign assets and accounts.",
       "India income tax return foreign assets disclosure resident",
       anchors=(r"schedule fa\b", r"foreign assets?"),
       require_any=_INDIA + (r"\bitr\b",)),
    _e("Form 67", ["form 67", "foreign tax credit india"],
       "Form 67 is used to claim credit in India for tax already paid abroad. It has to be filed with or before the return.",
       "India foreign tax credit income tax return",
       anchors=(r"\bform 67\b", r"foreign tax credit"),
       require_any=_INDIA + (r"\bitr\b",)),
    _e("Capital gains exemptions", ["54ec", "section 54", "section 54f", "54f"],
       "Sections 54, 54F and 54EC give exemption from capital gains tax if the sale proceeds are reinvested in a house or specified bonds.",
       "India capital gains tax exemption reinvest property NRI",
       anchors=(r"\b54EC\b", r"\b54F\b", r"section 54\b"),
       require_any=_INDIA + (r"capital gains?",)),
    _e("GIFT City", ["gift city", "gift ifsc", "ifsc gift"],
       "GIFT City is India's International Financial Services Centre in Gujarat, where NRIs can use some foreign-currency "
       "investment and banking products.",
       "India GIFT City IFSC NRI foreign currency investment",
       anchors=(r"\bGIFT City\b", r"\bGIFT IFSC\b", r"\bIFSC\b")),
    _e("PIS", ["PIS", "pis account", "portfolio investment scheme"],
       "A PIS (Portfolio Investment Scheme) account is a bank-linked account that lets NRIs buy and sell Indian shares under RBI rules.",
       "NRI India shares demat RBI portfolio investment scheme",
       anchors=(r"\bPIS\b", r"portfolio investment scheme"),
       require_any=_INDIA + (r"\bdemat\b",)),
    _e("Repatriation", ["repatriation", "repatriate"],
       "Repatriation means moving money from India back to an overseas bank account, subject to limits, taxes and paperwork "
       "that depend on the account type and where the money came from.",
       "NRI India money transfer abroad NRO NRE limit paperwork",
       anchors=(r"repatriat",),
       require_any=_INDIA + (r"\bnro\b", r"\bnre\b")),
    _e("Returning to India", ["moving back to india", "returning to india", "return to india", "relocating to india"],
       "Planning a move back to India: your tax residency status, converting NRE/NRO accounts to resident accounts and how "
       "foreign assets are taxed.",
       "NRI returning India RNOR resident status bank accounts foreign assets tax"),
)

# Capitalised words that are normal English, not acronyms to enforce.
_STOP = {"OR", "AND", "NOT", "THE", "FOR", "TO", "IN", "ON", "AT", "OF", "IS", "IT", "AS", "BY", "IF", "SO", "NO", "A", "I", "VS"}


@dataclass(frozen=True)
class Context:
    entries: tuple[Entry, ...]
    unknown_acronyms: tuple[str, ...]
    extra_words: str  # appended to the search query

    @property
    def filtering(self) -> bool:
        """True when results must be checked against the term."""
        return bool(self.unknown_acronyms) or any(e.anchors for e in self.entries)

    def keeps(self, text: str) -> bool:
        """Does this result text really talk about the term the visitor asked about?"""
        if not self.filtering:
            return True
        for e in self.entries:
            if e.anchors and not any(a.search(text) for a in e.anchors):
                continue
            if e.require_any and not any(r.search(text) for r in e.require_any):
                continue
            if e.anchors or e.require_any:
                return True
        return any(re.search(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])", text, re.IGNORECASE) for t in self.unknown_acronyms)

    def interpreted(self) -> list[dict]:
        return [{"term": e.name, "meaning": e.meaning} for e in self.entries]


def context_for(topic: str) -> Context:
    entries = tuple(e for e in ENTRIES if e.pattern.search(topic))
    known_text = " ".join(t for e in entries for t in e.terms).upper()
    unknown = tuple(
        tok
        for tok in dict.fromkeys(re.findall(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,5}(?![A-Za-z0-9])", topic))
        if tok not in _STOP and tok not in known_text.split() and not any(tok == t.upper() for e in entries for t in e.terms)
    )
    extra = " ".join(dict.fromkeys(w for e in entries[:2] for w in e.also.split()))
    return Context(entries, unknown, extra)
