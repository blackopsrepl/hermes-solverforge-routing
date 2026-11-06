/* Runs only inside SandboxedFrame's opaque-origin document. */
(function () {
  'use strict';
  var data = JSON.parse(document.getElementById('route-data').textContent);
  var status = document.getElementById('tiles');
  var ctrl = SF.map.create({container: 'map', center: data.markers.length ? [data.markers[0].coordinates[1], data.markers[0].coordinates[0]] : [0, 0], zoom: data.markers.length ? 13 : 2});
  var map = ctrl.map;
  var points = data.geometry.map(function (p) { return [p[1], p[0]]; });
  var bounds = L.latLngBounds(points);
  var groups = new Map();
  data.markers.forEach(function (p) {
    bounds.extend([p.coordinates[1], p.coordinates[0]]);
    var key = p.coordinates.join(',');
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  });
  groups.forEach(function (stops) {
    var first = stops[0];
    // The start reads S, as in the itinerary; unplanned stops carry no number yet.
    var badge = function (p) { return p.number === 0 ? 'S' : data.solved ? String(p.number) : '•'; };
    var marker = ctrl.addStopNumber({lat: first.coordinates[1], lng: first.coordinates[0], number: stops.map(badge).join('/'), color: '#144e87'});
    var text = document.createElement('span');
    text.textContent = stops.map(function (p) { return (data.solved || p.number === 0 ? badge(p) + ': ' : '') + p.title; }).join(' · ');
    marker.bindPopup(text);
    marker.getElement().setAttribute('aria-label', text.textContent);
    marker.getElement().setAttribute('title', text.textContent);
  });
  // Travel direction: the road carries its own lane paint, SimCity-style. One colour
  // per hop on a white casing, with chevrons stroked INTO the overlay so they sit
  // centred on the road and scale and pan with it like painted lane arrows.
  // The route is REAL road geometry. The lane paint (casing, colour, chevrons) is
  // screen-sized and repainted at every zoom, exactly like painted roads in a game:
  // it never drifts off the road regardless of the viewport scale.
  var LEG_COLORS = ["#1d4f9c", "#2e7cc4", "#63a4dd", "#9ec5e8"];
  var LANE_PX = 5;        // right-hand offset of the painted lane, in screen pixels
  var ARROW_PX = 16;      // chevron size, in screen pixels
  var ARROW_STEP_PX = 110; // spacing between chevrons, in screen pixels
  var lanePaint = [];      // layers added per render, removed at the next zoom
  function clearLanePaint() {
    while (lanePaint.length) {
      var layer = lanePaint.pop();
      if (layer.removeFrom) layer.removeFrom(map);
    }
  }
  function renderLanePaint() {
    var toLatLng = function (p) { return [p[0], p[1]]; };
    clearLanePaint();
    if (!(data.solved && data.markers.length)) {
      // No plan yet: a plain centred line, no lane paint, no arrows.
      var geo0 = data.geometry.map(function (p) { return [p[1], p[0]]; });
      if (geo0.length > 1) lanePaint.push(ctrl.drawRoute({ points: geo0, color: '#8fa8c0', weight: 4, opacity: 0.9 }));
      return;
    }
    var geo = data.geometry.map(function (p) { return [p[1], p[0]]; });
    var cut = function (id) { var m = /^geo:(\d+)_(\d+)$/.exec(id); return m ? [Number(m[1]), Number(m[2])] : null; };
    var legs = (data.legs || []).map(cut).filter(function (x) { return x && x[1] > x[0]; });
    if (!legs.length && geo.length > 1) legs = [[0, geo.length - 1]];
    var li = 0;
    legs.forEach(function (pair) {
      if (pair[1] - pair[0] < 1) return;
      var leg = geo.slice(pair[0], pair[1] + 1).map(toLatLng);
      if (leg.length < 2) return;
      var color = LEG_COLORS[li % LEG_COLORS.length];
      li++;
      // Offset in screen pixels around each sample point (right-hand side of travel).
      var pts = leg.map(function (p) { return map.latLngToContainerPoint(p); });
      var lane = pts.map(function (p, n) {
        var a = pts[Math.max(0, n - 1)], b = pts[Math.min(pts.length - 1, n + 1)];
        var dx = b.x - a.x, dy = b.y - a.y, len = Math.max(Math.sqrt(dx * dx + dy * dy), 1e-6);
        var u = { x: dx / len, y: dy / len };
        return map.containerPointToLatLng({ x: p.x - u.y * LANE_PX, y: p.y + u.x * LANE_PX });
      });
      lanePaint.push(ctrl.drawRoute({ points: lane, color: '#ffffff', weight: 9, opacity: 0.9 }));
      lanePaint.push(ctrl.drawRoute({ points: lane, color: color, weight: 5, opacity: 1 }));
      // Chevrons every ARROW_STEP_PX of screen distance along the painted lane.
      var screen = pts;
      var travelled = 0, nextAt = ARROW_STEP_PX, i = 1, prev = screen[0], placed = 0;
      while (i < screen.length && placed < 60) {
        var cur = screen[i];
        var seg = Math.hypot(cur.x - prev.x, cur.y - prev.y);
        if (seg === 0) { i++; continue; }
        if (travelled + seg < nextAt) { travelled += seg; prev = cur; i++; continue; }
        var t = (nextAt - travelled) / seg;
        var cx = prev.x + (cur.x - prev.x) * t, cy = prev.y + (cur.y - prev.y) * t;
        var deg = Math.round(Math.atan2(cur.y - prev.y, cur.x - prev.x) * 180 / Math.PI);
        var at = map.containerPointToLatLng({ x: cx, y: cy });
        var arrow = L.marker(at, {
          interactive: false,
          keyboard: false,
          icon: L.divIcon({
            className: 'route-arrow',
            iconSize: [ARROW_PX, ARROW_PX],
            iconAnchor: [ARROW_PX / 2, ARROW_PX / 2],
            html: '<svg width="' + ARROW_PX + '" height="' + ARROW_PX + '" style="display:block"><path d="M' + (ARROW_PX * 0.14) + ',' + (ARROW_PX * 0.26) + 'L' + (ARROW_PX * 0.88) + ',' + (ARROW_PX * 0.5) + 'L' + (ARROW_PX * 0.14) + ',' + (ARROW_PX * 0.74) + '" stroke="#fff" stroke-width="' + (ARROW_PX / 6) + '" fill="none" stroke-linecap="round" opacity="0.95" style="transform:rotate(' + deg + 'deg);transform-origin:' + (ARROW_PX / 2) + 'px ' + (ARROW_PX / 2) + 'px"></path></svg>'
          })
        }).addTo(map);
        lanePaint.push(arrow);
        placed++;
        nextAt += ARROW_STEP_PX;
        prev = { x: cx, y: cy };
      }
    });
  }
  if (data.geometry.length > 1) renderLanePaint();
  var zooming = false;
  var fitPending = false;
  var fit = function () {
    // Leaflet ignores a competing setView while its CSS zoom is in flight.
    // Queue Fit on public lifecycle events instead of touching private fields.
    if (zooming) { fitPending = true; return; }
    map.invalidateSize();
    if (bounds.isValid()) map.fitBounds(bounds, {padding: [42, 42], maxZoom: 16, animate: false});
  };
  map.on('zoomstart', function () { zooming = true; });
  map.on('zoomend', function () {
    zooming = false;
    if (fitPending) { fitPending = false; fit(); }
    renderLanePaint();
  });
  if (data.markers.length) {
    // Fit sits with the zoom buttons, as a map control, not in a toolbar.
    var FitControl = L.Control.extend({options: {position: 'topleft'}, onAdd: function () {
      var button = L.DomUtil.create('button', 'fit-control');
      button.type = 'button';
      button.textContent = 'Fit';
      button.title = 'Show the whole route';
      L.DomEvent.disableClickPropagation(button);
      L.DomEvent.on(button, 'click', fit);
      return button;
    }});
    map.addControl(new FitControl());
  }
  var failed = false;
  setTimeout(function () {
    if (!failed && !document.querySelector('.leaflet-tile-loaded')) {
      status.textContent = 'Map tiles unavailable.';
      status.className = 'warning';
    }
  }, 15000);
  map.eachLayer(function (layer) {
    if (!(layer instanceof L.TileLayer)) return;
    layer.on('tileerror', function () {
      failed = true;
      status.textContent = 'Map tiles unavailable.';
      status.className = 'warning';
    });
    layer.on('load', function () {
      if (!failed) status.textContent = '';
    });
    // Leaflet's stock attribution omits “contributors”. State it explicitly.
    layer.setAttribution && layer.setAttribution('© OpenStreetMap contributors');
  });
  var attribution = map.attributionControl;
  attribution.setPrefix(false);
  attribution.addAttribution('© OpenStreetMap contributors');
  new ResizeObserver(fit).observe(document.getElementById('map'));
  fit();
})();
