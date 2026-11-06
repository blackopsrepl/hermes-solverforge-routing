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
    var marker = ctrl.addStopNumber({lat: first.coordinates[1], lng: first.coordinates[0], number: stops.map(function (p) { return p.number; }).join('/'), color: '#144e87'});
    var text = document.createElement('span');
    text.textContent = stops.map(function (p) { return p.number + ': ' + p.title; }).join(' · ');
    marker.bindPopup(text);
    marker.getElement().setAttribute('aria-label', text.textContent);
    marker.getElement().setAttribute('title', text.textContent);
  });
  if (points.length) ctrl.drawRoute({points: points, color: '#1266ba', weight: 5, opacity: 0.9});
  var zooming = false;
  var fitPending = false;
  var fit = function () {
    // Leaflet ignores a competing setView while its CSS zoom is in flight.
    // Queue Fit on public lifecycle events instead of touching private fields.
    if (zooming) { fitPending = true; return; }
    map.invalidateSize();
    if (bounds.isValid()) map.fitBounds(bounds, {padding: [36, 36], maxZoom: 16, animate: false});
  };
  map.on('zoomstart', function () { zooming = true; });
  map.on('zoomend', function () {
    zooming = false;
    if (fitPending) { fitPending = false; fit(); }
  });
  document.getElementById('fit').addEventListener('click', fit);
  document.getElementById('summary').textContent = '';
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
