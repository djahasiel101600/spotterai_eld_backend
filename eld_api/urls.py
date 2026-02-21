from django.urls import path
from . import views

urlpatterns = [
    path('health/', views.health_check, name='health_check'),
    path('hos-config/', views.get_hos_configuration, name='get_hos_configuration'),
    path('geocode-route/', views.geocode_and_route, name='geocode_and_route'),  
    path('calculate-trip/', views.calculate_trip, name='calculate_trip'),
    path('trips/', views.trips_list, name='trips_list'),
    path('trips/<int:trip_id>/', views.trip_detail, name='trip_detail'),
    path('trips/<int:trip_id>/logs/', views.get_daily_logs, name='get_daily_logs'),
    path('trips/<int:trip_id>/fix-coordinates/', views.fix_trip_coordinates, name='fix_trip_coordinates'),
    path('test/', views.test_api, name='test_api'),
]