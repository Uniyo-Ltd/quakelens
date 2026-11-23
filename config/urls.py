from django.urls import path

from earthquakes.views import EarthquakeDetail, EarthquakeList, trend_detail

from .views import health


urlpatterns = [
    path("health/", health, name="health"),
    path("api/v1/earthquakes/", EarthquakeList.as_view(), name="earthquake-list"),
    path("api/v1/earthquakes/<str:external_id>/", EarthquakeDetail.as_view(), name="earthquake-detail"),
    path("api/v1/trends/", trend_detail, name="earthquake-trend"),
]
