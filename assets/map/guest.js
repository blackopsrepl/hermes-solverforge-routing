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
  var LEG_COLORS = ["#1d4f9c", "#2e7cc4", "#63a4dd", "#9ec5e8"];
  var LANE = 2.5;   // how far the painted lane sits right of the true line
  var toLatLng = function (p) { return [p[0], p[1]]; };
  // Right-hand traffic: every hop is drawn in its own right lane, so a round trip
  // that reuses a stretch reads as a two-way street the whole way.
  function laneLatLngs(map, leg) {
    var pts = leg.map(function (p) { return map.latLngToLayerPoint(p); });
    var out = [], i;
    var dir = function (a, b) {
      var dx = b.x - a.x, dy = b.y - a.y, len = Math.max(Math.sqrt(dx * dx + dy * dy), 1e-6);
      return { x: dx / len, y: dy / len };
    };
    for (i = 0; i < pts.length; i++) {
      var u = dir(pts[Math.max(0, i - 1)], pts[Math.min(pts.length - 1, i + 1)]);
      var p = { x: pts[i].x - u.y * LANE, y: pts[i].y + u.x * LANE };
      out.push(map.layerPointToLatLng(p));
    }
    return out;
  }
  function laneArrows(map, lane) {
    // Direction chevrons: road markers carrying a small SVG arrow, centred on the
    // lane, rotated by the screen-space bearing of the local road segment.
    var count = 0;
    var geoSpan = lane.length;
    var budget = Math.min(12, Math.ceil(geoSpan / Math.max(8, Math.floor(geoSpan / 45))));
    var step = Math.max(4, Math.floor(geoSpan / budget));
    for (var k = step; k < lane.length - Math.max(2, Math.floor(step / 2)); k += step) {
      if (count >= 45) break;
      if (!lane[k - 1] || !lane[k]) break;
      var a = map.latLngToContainerPoint(lane[k - 1]);
      var b = map.latLngToContainerPoint(lane[k]);
      var deg = Math.round(Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI);
      L.marker(lane[k], {
        interactive: false,
        keyboard: false,
        icon: L.divIcon({
          className: 'route-arrow',
          iconSize: [12, 12],
          iconAnchor: [6, 6],
          html: '<svg width="12" height="12" style="display:block"><path d="M1,3.2L11,6L1,8.8" stroke="#fff" stroke-width="2.4" fill="none" stroke-linecap="round" opacity="0.95" style="transform:translate(0,0) rotate(' + deg + 'deg);transform-origin:6px 6px"></path></svg>'
        })
      }).addTo(map);
      count++;
    }
  }
  function drawLegs() {
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
      var lane = laneLatLngs(map, leg);
      ctrl.drawRoute({ points: lane, color: '#ffffff', weight: 9, opacity: 0.9 });
      ctrl.drawRoute({ points: lane, color: data.solved ? color : '#8fa8c0', weight: 5, opacity: data.solved ? 1 : 0.9 });
      if (data.solved) laneArrows(map, lane);
    });
  }
  if (data.geometry.length > 1) drawLegs();
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
