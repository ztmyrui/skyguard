"""Contract and common layouts for workspace pages."""
from datetime import timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from ...flight_rules import DEMO_START


class Page(QWidget):
    """Define the lifecycle, toolbar and map interface for a workspace page."""

    title = ''
    uses_map = False
    map_changed = Signal()

    def __init__(self, context):
        super().__init__()
        self.context = context
        self.map_slot = None
        self.map_view = None
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)

    def context_tags(self):
        """Return the top bar labels as text and tone pairs."""
        return ()

    def actions(self):
        """Return the top bar actions as text, callback, icon and variant."""
        return ()

    def on_enter(self):
        """Restore page controls when entering the page."""

    def on_leave(self):
        """Release any page-specific interaction when leaving the page."""

    def refresh(self):
        """Refresh the page from its current data."""

    def map_payload(self):
        """Return map data for the visible page."""
        return {}

    def map_selection(self):
        """Return the event ID to highlight on the map."""
        return ''

    def map_selected(self, event_id):
        """Handle a map marker selection."""

    def map_clicked(self, lat, lon):
        """Handle a WGS84 map click."""

    def public_map_clicked(self, city, lat, lon):
        """Handle a public map click in provider coordinates."""

    def build_columns(self, left, right, tabs=None, *, tabs_height=200, tabs_minimum=200, map_footer=None,
                      toolbar=None):
        """Lay out side panels, a shared map slot and optional detail tabs."""
        columns = QSplitter(Qt.Horizontal)
        columns.setHandleWidth(8)
        columns.setChildrenCollapsible(False)
        left.setMinimumWidth(280)
        left.setMaximumWidth(340)
        right.setMinimumWidth(320)
        right.setMaximumWidth(380)
        center = QWidget()
        center.setMinimumSize(420,300)
        self.map_slot = QVBoxLayout(center)
        self.map_slot.setContentsMargins(0, 0, 0, 0)
        self.map_slot.setSpacing(4)
        middle = center
        if map_footer is not None:
            middle = QWidget()
            stack = QVBoxLayout(middle)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(5)
            stack.addWidget(center, 1)
            stack.addWidget(map_footer)
        for widget in (left, middle, right):
            columns.addWidget(widget)
        columns.setStretchFactor(1, 1)
        columns.setSizes([300, 640, 340])
        if toolbar is not None:
            self.body.addWidget(toolbar)
        if tabs is None:
            self.body.addWidget(columns, 1)
            return columns
        rows = QSplitter(Qt.Vertical)
        rows.setHandleWidth(8)
        rows.setChildrenCollapsible(False)
        tabs.setMinimumHeight(tabs_minimum)
        rows.addWidget(columns)
        rows.addWidget(tabs)
        rows.setStretchFactor(0, 1)
        rows.setStretchFactor(1, 0)
        rows.setSizes([600, tabs_height])
        self.body.addWidget(rows, 1)
        return columns


class DutyPage(Page):
    """Share duty context and session actions across the duty pages."""

    def context_tags(self):
        """Identify the city and its simulation context."""
        return ((self.context.civic.cities()[self.context.public_city]['name'], 'info'), ('模拟演练', 'demo'))

    def actions(self):
        """Expose the three existing duty record actions."""
        return (
            ('新增观察', self.context.observation_requested.emit, 'plus', 'primary'),
            ('导入记录', self.context.import_requested.emit, 'import', 'secondary'),
            ('导出档案', self.context.export_requested.emit, 'export', 'quiet'),
        )


class DutyMapPage(DutyPage):
    """Provide the shared duty map context and event selection."""

    uses_map = True
    shows_posts = False
    observation_location = Signal(float, float)

    def map_payload(self):
        """Combine the service scene with the current exercise overlays."""
        identifier = self.context.public_city
        city = self.context.civic.cities()[identifier]
        service = self.context.service
        payload = service.map_payload(include_grid=False)
        layers = self.map_view.layer_flags() if self.map_view else {}
        payload['observations'] = [dict(view, history=[], prediction=view['prediction'] if view['id']==self.context.selected else [])
                                   for view in payload['observations']
                                   if identifier=='changsha' and (view['id']==self.context.selected or layers.get('events'))]
        payload['areas'] = payload['areas'] if identifier=='changsha' and layers.get('areas') else []
        time = DEMO_START.replace(hour=0, minute=0) + timedelta(
            seconds=service.scene.start_of_day_s + service.scene.clock,
        )
        payload.update(
            city=identifier, center=city['center'], public=False,
            zones=[zone for zone in self.context.civic.book(identifier).overlays(time, True)
                   if zone['kind']=='restriction'],
            pins=[post for post in city['posts'] if post['enabled']] if layers.get('posts') else [],
        )
        return payload

    def map_selection(self):
        """Return the shared duty event selection."""
        return self.context.selected

    def map_selected(self, event_id):
        """Select the event in both duty map pages."""
        self.context.select(event_id)

    def map_clicked(self, lat, lon):
        """Offer the clicked location for an observation."""
        self.observation_location.emit(lat, lon)
