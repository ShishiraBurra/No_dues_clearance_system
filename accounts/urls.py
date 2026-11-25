from django.urls import path
from . import views

urlpatterns = [
    path('',views.login_view, name='login'),
    path('register/',views.register_view, name='register'),
    path('student/', views.student_view, name='student_view'),
    path('student/certificate/<int:profile_id>/', views.download_no_dues_certificate, name='download_no_dues_certificate'),
    path('in-charge/', views.in_charge_view, name='in_charge_view'),
    path('hod/', views.hod_view, name='hod_view'),
    path('admin-upload-roster/', views.upload_master_list_view, name='upload_master_list_view'),
    path('staff-registrations/', views.staff_registration_admin_view, name='staff_registration_admin_view'),
    path('api/due-update/', views.update_department_due_api, name='update_department_due_api'),
    path('api/hod-signoff/', views.update_hod_signoff_api, name='update_hod_signoff_api'),

    ]