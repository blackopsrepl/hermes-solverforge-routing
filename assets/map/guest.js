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
  // One line per travel hop, exactly as the engine routed it on the road graph.
  var LEG_COLORS = ['#1d4f9c', '#2e7cc4', '#63a4dd', '#9ec5e8'];
  (data.legs || []).forEach(function (leg, n) {
    ctrl.drawRoute({ points: leg.map(function (p) { return [p[1], p[0]]; }), color: LEG_COLORS[n % LEG_COLORS.length], weight: 5, opacity: 1 });
  });
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
