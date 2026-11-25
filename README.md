# No-Dues Clearance System

## Project overview

This project is a Django web application for managing college no-dues clearance. It maintains an approved college roster, provides separate portals for students, department in-charges, and Heads of Department (HODs), tracks department-by-department clearance, and generates a PDF certificate after final HOD approval.

The application is organized as a single Django project (`nodue`) with one primary app (`accounts`). It uses Django's built-in authentication and admin, and SQLite by default.

## Roles and capabilities

| Role | Main capabilities |
| --- | --- |
| Student | Register with a college-issued code; apply for clearance; view department status and rejection reasons; resubmit rejected checks; view academic-year records; download the final certificate after HOD approval. |
| Department in-charge | Review pending clearance applications for the assigned department and branch; clear or reject an application with a reason. |
| HOD | Review students whose department clearances are all complete; approve or reject final clearance; inspect the branch ledger and department progress. |
| College administrator | Use Django admin and the roster-upload page; issue student registration codes; review staff registration requests. The roster-upload and staff-request pages require a superuser account. |

## End-to-end workflows

### 1. Prepare the roster and registration codes

1. Sign in to `/admin/` with a Django superuser. Create one with `python manage.py createsuperuser` if necessary.
2. Open `/admin-upload-roster/`.
3. Upload one or more `.xlsx` files. Each sheet must have a header row with the required columns shown below. Values are checked for missing cells, maximum lengths, duplicates in the uploaded file, and (where applicable) valid year/mobile formats.
4. Upload the student registration-code sheet after the corresponding active student roster entries exist. The app stores a password hash of each code, not the original code.
5. To review staff sign-up requests, open `/staff-registrations/` and approve or reject each pending request.

#### Spreadsheet columns

| Upload | Required columns | Notes |
| --- | --- | --- |
| Students | `pin`, `academic_year`, `mobile_number`, `full_name`, `branch` | Academic year format is `YYYY-YYYY`; PIN/year identifies a student roster entry. |
| In-charges | `identity_code`, `mobile_number`, `full_name`, `assigned_department` | `assigned_department` must be one of the department codes listed below. |
| HODs | `identity_code`, `mobile_number`, `full_name`, `assigned_department` | Here, `assigned_department` contains the HOD's branch value; it is stored as the roster `student_branch`. |
| Student registration codes | `pin`, `academic_year`, `registration_code` | Each PIN/year must match an active student roster entry; each code in the file must be unique. |

The upload page processes `.xlsx` files. Keep PINs and identity codes in spreadsheet text cells when they may contain leading zeroes or formatting-sensitive values.

#### Department codes

| Code | Department |
| --- | --- |
| `civil` | Civil Section |
| `electric` | Electric Lab |
| `mechanical` | Mechanical Lab |
| `electronic` | Electronic Lab |
| `automobile` | Automobile Lab |
| `computer` | Computer Lab |
| `phy_lab` | Physics Lab |
| `chem_lab` | Chemistry Lab |
| `pharmacy` | Pharmacy |
| `library` | College Library |
| `office` | Office |
| `work_shop` | Work Shop |
| `hostel` | Hostel |
| `ncc` | N.C.C |
| `games` | Games and Sports |

### 2. Account registration and login

#### Student

1. Open `/register/` and enter the PIN, the mobile number recorded in the roster, a password and its confirmation, and the registration code issued for that PIN and academic year. The password fields must match.
2. The system checks the active roster entry and mobile number, validates and consumes the one-time registration code, creates a Django user and student role mapping, and creates the student's academic-year profile.
3. A dues record is created for each of the 15 departments. The student signs in at `/` (the login page) using the PIN as the username.
4. When a registered student signs in, the app provisions the latest active student roster year for that account if necessary. Older-year profiles are retained and shown read-only.

#### HOD or in-charge

1. Open `/register/`, enter the staff ID and rostered mobile number, and submit a staff registration request.
2. A college superuser reviews the request at `/staff-registrations/`. Approved staff return to `/register/`, enter the same ID and mobile number in the completion form, and set a password.
3. The app creates the staff user's role mapping and either an in-charge profile (assigned department) or an HOD profile (managed branch). The staff member can then sign in using the staff ID as the username.
4. A rejected request can be submitted again while the staff roster entry remains active.

### 3. Student applies for clearance

1. From `/student/`, the student selects an academic year if more than one profile is available.
2. The student submits the clearance application. Every `not_initiated` department record for that profile becomes `pending`.
3. The student dashboard shows each department's status and any rejection reason. The student cannot directly change a department's decision.

### 4. Department review

1. An in-charge signs in and opens `/in-charge/`. The account is restricted to its assigned department.
2. The in-charge selects an academic year and a branch tab. The queue is based on the student's PIN branch token (`cs`, `ec`, `me`, `ce`, `au`, or `ee`).
3. For an active academic year, pending requests can be cleared or rejected. A rejection requires a reason.
4. A cleared record becomes `cleared`; a rejected record becomes `rejected` and stores the reason. The student can resubmit rejected department records after addressing the issue; resubmission returns those records to `pending`.
5. Previous academic-year records are displayed without update controls.

### 5. HOD final review and certificate

1. The HOD signs in and opens `/hod/`. The app limits the view and final-signoff endpoint to the HOD's mapped branch. Available years can be selected.
2. The branch ledger shows fully cleared students, students still being processed (including pending and rejected department details), and inactive records.
3. A student enters the final review queue only when all 15 department records are `cleared`. The HOD can approve the final clearance or reject it with a reason.
4. If the HOD rejects, the student must use the HOD resubmission action after addressing the issue. The profile returns to the HOD's review queue.
5. Once approved, the student can download the PDF certificate from their dashboard. The download checks that the signed-in user owns the profile, that HOD approval exists, and that all 15 department records remain cleared.

## Clearance status lifecycle

Department records use these states:

```text
not_initiated --student applies--> pending
pending --in-charge clears-------> cleared
pending --in-charge rejects------> rejected
rejected --student resubmits-----> pending
```

Final HOD approval is stored on the student profile separately from department statuses. A certificate requires both HOD approval and all department records in `cleared` state. HOD rejection records a reason and requires student resubmission before the HOD can reconsider it.

## Routes

| URL | Purpose |
| --- | --- |
| `/` | Login |
| `/register/` | Student and staff registration |
| `/student/` | Student dashboard and clearance application |
| `/student/certificate/<profile_id>/` | Download the signed-in student's approved PDF certificate |
| `/in-charge/` | Department in-charge dashboard |
| `/hod/` | HOD dashboard and branch ledger |
| `/admin-upload-roster/` | Superuser roster and registration-code upload |
| `/staff-registrations/` | Superuser staff-registration review |
| `/api/due-update/` | CSRF-protected JSON endpoint for in-charge decisions |
| `/api/hod-signoff/` | CSRF-protected JSON endpoint for HOD decisions |
| `/admin/` | Django administration |

## Data model

The main models are in [`accounts/models.py`](accounts/models.py):

- `CollegeMasterList`: authorized student/staff roster entries and active-batch information.
- `CollegeRegistrationCode`: one-time, hashed student registration codes with usage tracking.
- `UserRoleMapping`: associates a Django user with `student`, `in_charge`, or `hod`.
- `StudentProfile`: a student's PIN, branch, academic year, activity state, and final HOD approval/resubmission details. A PIN is unique per academic year, case-insensitively.
- `InChargeProfile` and `HODProfile`: staff identity and assigned department or branch.
- `DuesRecord`: one student's status and remarks for one department; unique per student and department.
- `StaffRegistrationRequest`: approval lifecycle for HOD and in-charge registrations.

Schema changes are tracked under [`accounts/migrations/`](accounts/migrations/). The Django admin also provides roster, profile, dues, code-usage, and registration-request management; roster and dues models support import/export in the admin.

## Project layout

```text
manage.py                    Django management entry point
nodue/                       Project settings, root URLs, ASGI and WSGI
accounts/models.py           Roles, roster, student profiles, and clearance data
accounts/views.py            Registration, dashboards, imports, APIs, and PDF output
accounts/urls.py             Application routes
accounts/admin.py            Django admin configuration
accounts/migrations/         Database schema and data migrations
accounts/templates/accounts/ HTML templates for the user portals
accounts/static/accounts/    Account-app static assets
templates/admin/             Django admin template overrides
db.sqlite3                   Default local SQLite database
```

## Local setup

Create and activate a virtual environment, then install the packages used by the app:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open `http://127.0.0.1:8000/` to sign in. The roster upload needs `pandas` and an Excel reader such as `openpyxl`; the admin uses `django-import-export`.

Run the account app tests with:

```powershell
python manage.py test accounts
```

## Configuration and deployment notes

- The default database is `db.sqlite3` at the project root. Configure `DATABASES` in [`nodue/settings.py`](nodue/settings.py) to use another database.
- The checked-in settings have `DEBUG = True`, an insecure development `SECRET_KEY`, and an empty `ALLOWED_HOSTS`. Do not deploy with these values. Use environment-based secrets and production settings, set allowed hosts, configure HTTPS/security options, and choose a production database and static-file serving strategy.
- The configured time zone is UTC.
- No production email integration is configured; do not assume registration or clearance events send email.
- Student and staff roster spreadsheets contain personal information. Restrict access to uploads and the database, and avoid using real student data in development.
