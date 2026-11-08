# Position from planets: offline benchmark [simcam] (24 positions, 0.6 min wall)

Epoch TDB JD 2461352.5. Hidden observer: 0.7-2 AU from the Sun, ecliptic latitude +/-3 deg, more than 0.05 AU from any planet. Bodies: Mercury, Venus, Earth, Moon, Mars, Jupiter and Saturn barycentres (DE421), visible if mag <= 5.5 and inside one of 8 ecliptic scan frames (1920x1080, 50 deg FOV, boresights at Sun elongation 40, 80, ..., 320 deg; no pointing within 35 deg of the Sun).

- **geometry**: exact light-time-corrected directions + Gaussian noise sigma = 20 arcsec per axis, shuffled, then identified and solved (checks the maths without the renderer).
- **rendered**: each scan frame is rendered by sim.sky with the planets as extra blobs (noise 0 DN, filmed by camera.fakecam with the fitted homography), solved by tetra3 (same centroider/solver settings as solve_live), blobs more than 4 px from every catalogue star (mag <= 6.5) are planet candidates, merged across overlapping frames, identified and solved.
- **+Sun**: a Sun direction (sun sensor, sigma 20 arcsec) is added as a known body at the origin.
- fix = least squares weighted by 1/(sigma_i r_i)^2, light-time corrected. Ellipsoid = 1-sigma semi-axes of (sum (I - u u^T)/(sigma_i r_i)^2)^-1 with sigma = 20 arcsec (geometry), 10 arcsec (rendered planets), 20 arcsec (Sun). NEES = e^T C^-1 e (should average ~3 if the ellipsoid is honest).
- ID ok = every assigned candidate got its true body (candidates labelled by the nearest true body within 0.1 deg).

| mode | Sun | fixes | median err km | median err AU | 90% err km | median ellipsoid a/b/c km | mean NEES | ID ok | bodies used (median) | bodies visible (median) |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| rendered | no | 24/24 | 22,897 | 1.53e-04 | 38,156 | 41,091 / 7,613 / 7,286 | 40.7 | 23/24 | 4 | 5 |
| rendered | yes | 24/24 | 15,080 | 1.01e-04 | 38,663 | 23,275 / 7,305 / 6,869 | 2.3 | 24/24 | 5 | 5 |

Rendered pipeline: 192/192 scan frames locked, median attitude error 12 arcsec from tetra3, 8.8 arcsec after the least-squares refit (total rotation, roll included); median planet-candidate direction error 8.0 arcsec; median 19 planet candidates per scan, 4 of them real planets.

## Per position (rendered, planets only)

| case | r AU | visible | used | err km | ellipsoid a/b/c km | NEES | resid RMS arcsec | ID ok |
|---:|---:|---|---|---:|---|---:|---:|---|
| 0 | 1.17 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 35,636 | 48,411 / 16,359 / 15,499 | 1.2 | 5 | yes |
| 1 | 1.15 | Venus+Earth+Moon+Mars+Saturn | Earth+Moon+Saturn+Venus+Mars | 12,799 | 41,045 / 7,190 / 7,083 | 2.5 | 3 | yes |
| 2 | 1.18 | Earth+Moon+Mars+Jupiter+Saturn | Saturn+Mars+Jupiter+Earth+Moon | 9,305 | 13,302 / 2,741 / 2,685 | 1.6 | 4 | yes |
| 3 | 0.99 | Earth+Moon+Jupiter+Saturn | Earth+Moon+Jupiter+Saturn | 24,574 | 43,491 / 13,503 / 12,896 | 2.4 | 3 | yes |
| 4 | 1.35 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Venus+Saturn+Jupiter+Mars | 19,422 | 41,453 / 6,701 / 6,615 | 4.2 | 6 | yes |
| 5 | 0.80 | Venus+Earth+Moon+Mars+Jupiter | Earth+Moon+Jupiter+Venus+Mars | 23,067 | 17,552 / 7,208 / 6,668 | 5.7 | 4 | yes |
| 6 | 0.81 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Venus+Mars+Jupiter+Saturn+Mercury | 5,056 | 7,108 / 4,186 / 3,608 | 0.6 | 8 | yes |
| 7 | 1.08 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Venus+Jupiter+Mars+Saturn+Mercury | 2,241 | 5,149 / 3,900 / 3,109 | 0.4 | 7 | yes |
| 8 | 1.10 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 31,512 | 51,648 / 16,853 / 16,023 | 1.2 | 2 | yes |
| 9 | 1.26 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Saturn+Venus+Jupiter+Mars | 11,921 | 35,746 / 5,506 / 5,442 | 1.9 | 9 | yes |
| 10 | 1.87 | Venus+Earth+Moon+Jupiter+Saturn | Earth+Moon+Jupiter+Venus+Saturn | 45,892 | 48,196 / 7,564 / 7,474 | 2.6 | 5 | yes |
| 11 | 1.03 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Saturn+Mars+Venus+Jupiter+Earth+Moon | 3,644 | 5,643 / 2,157 / 2,018 | 1.1 | 6 | yes |
| 12 | 1.15 | Venus+Earth+Moon+Mars+Saturn | Earth+Moon+Saturn+Venus+Mars | 22,727 | 41,138 / 7,207 / 7,099 | 2.9 | 5 | yes |
| 13 | 0.91 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 12,174 | 47,036 / 14,849 / 14,161 | 0.7 | 6 | yes |
| 14 | 1.29 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Venus+Earth+Moon+Saturn+Mars | 699,304 | 34,126 / 5,100 / 5,044 | 918.9 | 86 | NO |
| 15 | 1.80 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 24,030 | 58,541 / 21,044 / 19,805 | 1.3 | 4 | yes |
| 16 | 1.02 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 25,470 | 54,001 / 17,020 / 16,234 | 1.8 | 4 | yes |
| 17 | 0.90 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 28,701 | 47,326 / 14,885 / 14,200 | 0.5 | 8 | yes |
| 18 | 1.67 | Venus+Earth+Moon+Mars+Saturn | Earth+Moon+Saturn+Venus+Mars | 20,065 | 51,376 / 8,769 / 8,644 | 4.3 | 4 | yes |
| 19 | 1.41 | Venus+Earth+Moon+Mars+Jupiter | Earth+Moon+Jupiter+Venus+Mars | 33,529 | 20,065 / 8,719 / 7,997 | 7.9 | 4 | yes |
| 20 | 1.11 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Jupiter+Mars+Venus+Saturn+Mercury | 9,381 | 9,172 / 5,345 / 4,619 | 1.1 | 8 | yes |
| 21 | 1.26 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Jupiter+Venus+Mars+Saturn | 16,814 | 16,238 / 7,662 / 6,930 | 4.9 | 8 | yes |
| 22 | 1.53 | Venus+Earth+Moon+Mars+Jupiter | Earth+Moon+Jupiter+Venus+Mars | 39,236 | 20,357 / 8,916 / 8,167 | 7.5 | 5 | yes |
| 23 | 1.07 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 29,617 | 50,939 / 16,542 / 15,735 | 0.7 | 6 | yes |
