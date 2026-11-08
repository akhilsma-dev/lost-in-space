# Position from planets: offline benchmark (24 positions, 0.8 min wall)

Epoch TDB JD 2461352.5. Hidden observer: 0.7-2 AU from the Sun, ecliptic latitude +/-3 deg, more than 0.05 AU from any planet. Bodies: Mercury, Venus, Earth, Moon, Mars, Jupiter and Saturn barycentres (DE421), visible if mag <= 5.5 and inside one of 8 ecliptic scan frames (1920x1080, 50 deg FOV, boresights at Sun elongation 40, 80, ..., 320 deg; no pointing within 35 deg of the Sun).

- **geometry**: exact light-time-corrected directions + Gaussian noise sigma = 20 arcsec per axis, shuffled, then identified and solved (checks the maths without the renderer).
- **rendered**: each scan frame is rendered by sim.sky with the planets as extra blobs (noise 0 DN), solved by tetra3 (same centroider/solver settings as solve_live), blobs more than 4 px from every catalogue star (mag <= 6.5) are planet candidates, merged across overlapping frames, identified and solved.
- **+Sun**: a Sun direction (sun sensor, sigma 20 arcsec) is added as a known body at the origin.
- fix = least squares weighted by 1/(sigma_i r_i)^2, light-time corrected. Ellipsoid = 1-sigma semi-axes of (sum (I - u u^T)/(sigma_i r_i)^2)^-1 with sigma = 20 arcsec (geometry), 5 arcsec (rendered planets), 20 arcsec (Sun). NEES = e^T C^-1 e (should average ~3 if the ellipsoid is honest).
- ID ok = every assigned candidate got its true body (candidates labelled by the nearest true body within 0.1 deg).

| mode | Sun | fixes | median err km | median err AU | 90% err km | median ellipsoid a/b/c km | mean NEES | ID ok | bodies used (median) | bodies visible (median) |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| geometry | no | 23/24 | 25,578 | 1.71e-04 | 146,620 | 31,206 / 9,392 / 8,919 | 3.7 | 23/23 | 5 | 6 |
| geometry | yes | 24/24 | 18,194 | 1.22e-04 | 43,042 | 19,235 / 9,206 / 8,585 | 2.6 | 24/24 | 7 | 6 |
| rendered | no | 23/24 | 8,135 | 5.44e-05 | 18,055 | 8,910 / 2,348 / 2,327 | 3.2 | 23/23 | 5 | 6 |
| rendered | yes | 24/24 | 7,053 | 4.71e-05 | 17,412 | 8,289 / 2,726 / 2,471 | 3.0 | 24/24 | 6 | 6 |

Rendered pipeline: 192/192 scan frames locked, median attitude error 12 arcsec from tetra3, 2.6 arcsec after the least-squares refit (total rotation, roll included); median planet-candidate direction error 1.9 arcsec; median 6 planet candidates per scan, 5 of them real planets.
No fix in 1 rendered scans (visible bodies: Mars+Jupiter).

## Per position (rendered, planets only)

| case | r AU | visible | used | err km | ellipsoid a/b/c km | NEES | resid RMS arcsec | ID ok |
|---:|---:|---|---|---:|---|---:|---:|---|
| 0 | 1.37 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Saturn+Earth+Moon+Venus+Jupiter+Mars | 44,443 | 20,592 / 3,138 / 3,102 | 11.0 | 4 | yes |
| 1 | 1.02 | Venus+Earth+Moon+Jupiter+Saturn | Jupiter+Saturn+Earth+Moon+Venus | 7,052 | 21,853 / 4,356 / 4,272 | 2.0 | 4 | yes |
| 2 | 0.89 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Saturn+Earth+Moon+Venus+Mercury+Jupiter+Mars | 11,565 | 5,668 / 1,910 / 1,811 | 6.7 | 4 | yes |
| 3 | 1.17 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Saturn+Jupiter+Mars+Earth+Moon+Venus | 1,545 | 6,355 / 903 / 894 | 1.3 | 11 | yes |
| 4 | 0.70 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Mercury+Venus+Saturn+Mars+Jupiter | 1,917 | 3,324 / 1,799 / 1,584 | 0.8 | 4 | yes |
| 5 | 0.75 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Saturn+Earth+Moon+Venus+Jupiter+Mars+Mercury | 525 | 1,133 / 841 / 676 | 0.6 | 2 | yes |
| 6 | 0.92 | Venus+Earth+Moon+Jupiter+Saturn | Jupiter+Saturn+Earth+Moon+Venus | 11,493 | 21,107 / 4,218 / 4,136 | 6.0 | 4 | yes |
| 7 | 1.26 | Venus+Earth+Moon+Mars+Saturn | Saturn+Earth+Moon+Venus+Mars | 29,256 | 22,026 / 3,824 / 3,768 | 6.6 | 6 | yes |
| 8 | 1.37 | Venus+Earth+Moon+Jupiter+Saturn | Jupiter+Saturn+Earth+Moon+Venus | 19,060 | 24,228 / 5,013 / 4,909 | 4.3 | 5 | yes |
| 9 | 0.76 | Mercury+Venus+Earth+Moon+Mars+Saturn | Saturn+Earth+Moon+Mercury+Venus+Mars | 13,854 | 9,116 / 2,239 / 2,174 | 4.8 | 4 | yes |
| 10 | 1.04 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Saturn+Earth+Moon+Venus+Mercury+Jupiter+Mars | 7,208 | 7,026 / 2,097 / 2,010 | 2.1 | 3 | yes |
| 11 | 1.47 | Mars+Jupiter | - | no fix | | | | |
| 12 | 1.30 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Venus+Saturn+Mars+Jupiter | 8,539 | 8,912 / 4,034 / 3,675 | 3.1 | 3 | yes |
| 13 | 0.72 | Mercury+Venus+Earth+Moon+Mars+Saturn | Saturn+Earth+Moon+Venus+Mercury+Mars | 14,035 | 8,910 / 2,192 / 2,129 | 4.3 | 4 | yes |
| 14 | 1.81 | Venus+Earth+Moon+Mars+Saturn | Saturn+Earth+Moon+Venus+Mars | 10,556 | 26,643 / 4,749 / 4,676 | 2.8 | 5 | yes |
| 15 | 1.92 | Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Saturn+Jupiter+Mars | 7,065 | 4,227 / 3,377 / 2,639 | 2.9 | 2 | yes |
| 16 | 0.72 | Mars+Jupiter+Saturn | Mars+Jupiter+Saturn | 9,682 | 24,636 / 7,289 / 6,990 | 1.2 | 2 | yes |
| 17 | 1.79 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Venus+Saturn+Mars+Jupiter | 8,135 | 10,479 / 4,602 / 4,214 | 2.8 | 4 | yes |
| 18 | 0.82 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Saturn+Earth+Moon+Jupiter+Venus+Mars+Mercury | 494 | 1,539 / 806 / 714 | 0.2 | 16 | yes |
| 19 | 1.65 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Saturn+Earth+Moon+Venus+Jupiter+Mars | 9,784 | 17,437 / 2,348 / 2,327 | 5.3 | 4 | yes |
| 20 | 0.86 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Saturn+Earth+Moon+Jupiter+Mercury+Venus+Mars | 948 | 3,882 / 1,592 / 1,473 | 0.1 | 2 | yes |
| 21 | 0.90 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Saturn+Earth+Moon+Jupiter+Mars+Mercury+Venus | 393 | 2,655 / 1,240 / 1,124 | 0.0 | 5 | yes |
| 22 | 0.87 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Mercury+Venus+Saturn+Mars+Jupiter | 3,762 | 4,379 / 2,346 / 2,068 | 2.6 | 3 | yes |
| 23 | 1.89 | Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Saturn+Jupiter+Mars | 5,678 | 4,967 / 3,379 / 2,794 | 2.3 | 1 | yes |
