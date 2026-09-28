# from .AutoSensorControlRoute import router as main_sensor_control_router
from .authRoute import router as auth_router
from .AdminPanelRoute import router as admin_panel_router
from .userRoute import router as user_dashboard_router
from .websockets import router as pond_device_websocket_router
from .applicationComponentsRoutes import router as application_component_router
from .buttonRoutes import router as button_router

# List of all routers
routers = [
    # main_sensor_control_router,
    auth_router,
    admin_panel_router,
    user_dashboard_router,
    pond_device_websocket_router,
    application_component_router,
    button_router
]


# # main_sensor_control_router contains all the routes for the application