"""Random fictional identities and document data."""
from __future__ import annotations

import random
import string
from dataclasses import dataclass, field
from datetime import date, timedelta

SURNAMES = ['GUPTA', 'SINGH', 'MEHTA', 'KHAN', 'SHARMA', 'PARKER', 'IYER', 'NAIR', 'REDDY', 'DAS', 'BOSE', 'KAPOOR',
            'CHOUDHARY', 'VERMA', 'JOSHI', 'PATEL', 'THAPA', 'RAHMAN', 'SILVA', 'PERERA', 'FERNANDO', 'MULLER', 'SMITH',
            'JOHNSON', 'GARCIA', 'MARTIN', 'NGUYEN', 'KIM', 'TANAKA', 'WANG', 'LI', 'OKAFOR', 'MENSAH', 'IVANOVA',
            'KOWALSKI', 'ROSSI', 'DUBOIS', 'ANDERSSON', 'OLIVEIRA', 'HASSAN', 'ABDULLAH', 'BANERJEE', 'MUKHERJEE',
            'CHATTERJEE', 'PILLAI', 'MENON', 'SAXENA', 'AGARWAL', 'TRIPATHI', 'PANDEY', 'YADAV', 'ACHARYA', 'GURUNG',
            'O CONNOR', 'VAN DER BERG', 'DE SOUZA', 'MACKENZIE', 'WOJCIECHOWSKA', 'FITZGERALD', 'SUBRAMANIAM']
GIVEN = ['AKSHIT', 'RHEA', 'ANIKA', 'VIKRAM', 'SANA', 'DEV', 'RAJ', 'ELENA', 'ARJUN', 'PRIYA', 'KAVYA', 'ROHAN',
         'MEERA', 'ADITYA', 'ISHAAN', 'NEHA', 'ZARA', 'KABIR', 'AISHA', 'FATIMA', 'OMAR', 'JOHN', 'MARY', 'JAMES',
         'SOFIA', 'LUCAS', 'EMMA', 'NOAH', 'OLIVIA', 'LIAM', 'YUKI', 'HIRO', 'MIN', 'JUN', 'CHIDI', 'AMARA',
         'OLGA', 'PIOTR', 'MARCO', 'CLAIRE', 'ERIK', 'ANA', 'JOAO', 'LAKSHMI', 'SURESH', 'GANESH', 'DEEPIKA',
         'HARPREET', 'GURPREET', 'SIDDHARTH', 'ANANYA', 'TENZIN', 'PEMA', 'NASRIN', 'TARIQ', 'MOHAMMED', 'XAVIER']
# Holder nationalities (real ICAO codes are fine for the *holder*; the issuer is always fictional).
NATIONALITIES = {'IND': 'INDIAN', 'NPL': 'NEPALESE', 'BGD': 'BANGLADESHI', 'LKA': 'SRI LANKAN', 'BTN': 'BHUTANESE',
                 'USA': 'AMERICAN', 'GBR': 'BRITISH', 'DEU': 'GERMAN', 'FRA': 'FRENCH', 'JPN': 'JAPANESE',
                 'CHN': 'CHINESE', 'RUS': 'RUSSIAN', 'AUS': 'AUSTRALIAN', 'CAN': 'CANADIAN', 'BRA': 'BRAZILIAN',
                 'NGA': 'NIGERIAN', 'ARE': 'EMIRATI', 'SGP': 'SINGAPOREAN', 'UTO': 'UTOPIAN', 'XXA': 'STATELESS'}
VISA_TYPES = ['TOURIST', 'BUSINESS', 'EMPLOYMENT', 'STUDENT', 'MEDICAL', 'CONFERENCE', 'TRANSIT', 'JOURNALIST',
              'RESEARCH', 'ENTRY']
ENTRIES = ['SINGLE', 'DOUBLE', 'MULTIPLE']
PERMIT_TYPES = ['WORK PERMIT', 'STUDENT RESIDENCE', 'FAMILY REUNION', 'LONG TERM RESIDENCE', 'SEASONAL WORK']
DL_CATEGORIES = ['A', 'A1', 'B', 'B1', 'C', 'C1', 'D', 'BE', 'CE', 'LMV', 'MCWG', 'HMV']
PLACES = ['NEW DELHI', 'MUMBAI', 'KOLKATA', 'CHENNAI', 'BENGALURU', 'KATHMANDU', 'DHAKA', 'COLOMBO', 'LONDON',
          'BERLIN', 'PARIS', 'TOKYO', 'SYDNEY', 'TORONTO', 'LAGOS', 'DUBAI', 'SINGAPORE', 'UTOPIA CITY', 'AMRITSAR',
          'LUCKNOW', 'JAIPUR', 'PATNA', 'GUWAHATI', 'SHILLONG', 'IMPHAL']


def rand_docno(rng: random.Random, prefix_letters: int | None = None) -> str:
    n = rng.choice([8, 9, 9, 9]) if prefix_letters is None else 9
    letters = rng.choice([1, 1, 2, 0]) if prefix_letters is None else prefix_letters
    head = ''.join(rng.choice(string.ascii_uppercase) for _ in range(letters))
    tail = ''.join(rng.choice(string.ascii_uppercase + string.digits * 3) for _ in range(n - letters))
    return (head + tail)[:9]


def rand_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, max(0, (end - start).days)))


@dataclass
class Person:
    surname: str
    given_names: str
    sex: str
    nationality: str
    date_of_birth: date
    place_of_birth: str
    face_seed: int
    extra: dict = field(default_factory=dict)


def random_person(rng: random.Random, today: date) -> Person:
    sur = rng.choice(SURNAMES)
    if rng.random() < 0.15:
        sur = sur + ' ' + rng.choice(SURNAMES)
    given = ' '.join(rng.sample(GIVEN, rng.choice([1, 1, 2, 2, 3])))
    sex = rng.choice(['M', 'F', 'M', 'F', 'X']) if rng.random() < 0.97 else '<'
    dob = rand_date(rng, date(today.year - 85, 1, 1), date(today.year - 1, 12, 28))
    return Person(sur, given, sex if sex != '<' else 'X', rng.choice(list(NATIONALITIES)), dob,
                  rng.choice(PLACES), rng.randrange(1 << 30))


def document_dates(rng: random.Random, today: date, validity_years: int, expired_rate: float = 0.12) -> tuple[date, date]:
    if rng.random() < expired_rate:
        expiry = rand_date(rng, today - timedelta(days=3 * 365), today - timedelta(days=1))
    else:
        expiry = rand_date(rng, today + timedelta(days=1), today + timedelta(days=validity_years * 365))
    try:
        issue = expiry.replace(year=expiry.year - validity_years) + timedelta(days=rng.choice([-1, 0, 0, 1]))
    except ValueError:
        issue = expiry - timedelta(days=validity_years * 365)
    return issue, expiry


def yymmdd(d: date) -> str:
    return d.strftime('%y%m%d')
