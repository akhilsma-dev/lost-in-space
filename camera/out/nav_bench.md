# Position from planets: offline benchmark (24 positions, 0.5 min wall)

Epoch TDB JD 2461352.5. Hidden observer: 0.7-2 AU from the Sun, ecliptic latitude +/-3 deg, more than 0.05 AU from any planet. Bodies: Mercury, Venus, Earth, Moon, Mars, Jupiter and Saturn barycentres (DE421), visible if mag <= 5.5 and inside one of 8 ecliptic scan frames (1920x1080, 50 deg FOV, boresights at Sun elongation 40, 80, ..., 320 deg; no pointing within 35 deg of the Sun).

- **geometry**: exact light-time-corrected directions + Gaussian noise sigma = 20 arcsec per axis, shuffled, then identified and solved (checks the maths without the renderer).
- **rendered**: each scan frame is rendered by sim.sky with the planets as extra blobs (noise 0 DN), solved by tetra3 (same centroider/solver settings as solve_live), blobs more than 4 px from every catalogue star (mag <= 6.5) are planet candidates, merged across overlapping frames, identified and solved.
- **+Sun**: a Sun direction (sun sensor, sigma 20 arcsec) is added as a known body at the origin.
- fix = least squares weighted by 1/(sigma_i r_i)^2, light-time corrected. Ellipsoid = 1-sigma semi-axes of (sum (I - u u^T)/(sigma_i r_i)^2)^-1 with sigma = 20 arcsec (geometry), 5 arcsec (rendered planets), 20 arcsec (Sun). NEES = e^T C^-1 e (should average ~3 if the ellipsoid is honest).
- ID ok = every assigned candidate got its true body (candidates labelled by the nearest true body within 0.1 deg).

| mode | Sun | fixes | median err km | median err AU | 90% err km | median ellipsoid a/b/c km | mean NEES | ID ok | bodies used (median) | bodies visible (median) |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| geometry | no | 24/24 | 31,589 | 2.11e-04 | 129,063 | 75,557 / 13,087 / 12,473 | 2.6 | 24/24 | 5 | 5 |
| geometry | yes | 24/24 | 28,900 | 1.93e-04 | 82,133 | 34,138 / 11,517 / 11,129 | 3.8 | 24/24 | 5 | 5 |
| rendered | no | 24/24 | 10,060 | 6.72e-05 | 16,806 | 20,649 / 3,807 / 3,644 | 2.1 | 24/24 | 4 | 5 |
| rendered | yes | 24/24 | 9,719 | 6.50e-05 | 25,270 | 16,244 / 3,768 / 3,587 | 2.3 | 24/24 | 5 | 5 |

Rendered pipeline: 192/192 scan frames locked, median attitude error 12 arcsec from tetra3, 2.0 arcsec after the least-squares refit (total rotation, roll included); median planet-candidate direction error 1.9 arcsec; median 5 planet candidates per scan, 4 of them real planets.

## Per position (rendered, planets only)

| case | r AU | visible | used | err km | ellipsoid a/b/c km | NEES | resid RMS arcsec | ID ok |
|---:|---:|---|---|---:|---|---:|---:|---|
| 0 | 1.17 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 3,849 | 24,204 / 8,179 / 7,749 | 0.2 | 0 | yes |
| 1 | 1.15 | Venus+Earth+Moon+Mars+Saturn | Earth+Moon+Venus+Saturn+Mars | 17,413 | 20,523 / 3,595 / 3,541 | 3.4 | 4 | yes |
| 2 | 1.18 | Earth+Moon+Mars+Jupiter+Saturn | Jupiter+Mars+Saturn+Earth+Moon | 2,703 | 6,650 / 1,371 / 1,343 | 2.7 | 2 | yes |
| 3 | 0.99 | Earth+Moon+Jupiter+Saturn | Earth+Moon+Jupiter+Saturn | 13,094 | 21,746 / 6,752 / 6,448 | 3.9 | 2 | yes |
| 4 | 1.35 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Saturn+Venus+Jupiter+Mars | 45,577 | 20,729 / 3,351 / 3,308 | 10.2 | 4 | yes |
| 5 | 0.80 | Venus+Earth+Moon+Mars+Jupiter | Earth+Moon+Venus+Jupiter+Mars | 3,372 | 8,777 / 3,604 / 3,334 | 1.0 | 2 | yes |
| 6 | 0.81 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Jupiter+Mars+Venus+Mercury+Saturn | 2,042 | 3,554 / 2,093 / 1,804 | 1.0 | 3 | yes |
| 7 | 1.08 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Mercury+Mars+Venus+Jupiter+Saturn | 3,354 | 2,575 / 1,950 / 1,554 | 1.9 | 4 | yes |
| 8 | 1.10 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 11,118 | 25,823 / 8,425 / 8,011 | 0.4 | 2 | yes |
| 9 | 1.26 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Saturn+Venus+Jupiter+Mars | 13,611 | 17,873 / 2,753 / 2,721 | 2.0 | 3 | yes |
| 10 | 1.87 | Venus+Earth+Moon+Jupiter+Saturn | Earth+Moon+Venus+Jupiter+Saturn | 18,934 | 24,102 / 3,783 / 3,738 | 2.5 | 4 | yes |
| 11 | 1.03 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Jupiter+Saturn+Venus+Mars+Earth+Moon | 1,144 | 2,821 / 1,078 / 1,009 | 0.2 | 1 | yes |
| 12 | 1.15 | Venus+Earth+Moon+Mars+Saturn | Earth+Moon+Saturn+Venus+Mars | 14,525 | 20,568 / 3,603 / 3,549 | 3.3 | 4 | yes |
| 13 | 0.91 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 2,973 | 23,518 / 7,425 / 7,081 | 0.1 | 1 | yes |
| 14 | 1.29 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Saturn+Mars | 10,376 | 28,410 / 3,308 / 3,285 | 1.8 | 2 | yes |
| 15 | 1.80 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 13,244 | 29,270 / 10,522 / 9,902 | 0.2 | 1 | yes |
| 16 | 1.02 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 14,450 | 27,001 / 8,510 / 8,117 | 0.3 | 2 | yes |
| 17 | 0.90 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 6,284 | 23,662 / 7,442 / 7,099 | 0.2 | 1 | yes |
| 18 | 1.67 | Venus+Earth+Moon+Mars+Saturn | Earth+Moon+Venus+Saturn+Mars | 15,392 | 25,688 / 4,384 / 4,322 | 3.3 | 6 | yes |
| 19 | 1.41 | Venus+Earth+Moon+Mars+Jupiter | Earth+Moon+Venus+Jupiter+Mars | 5,525 | 10,033 / 4,360 / 3,999 | 1.2 | 3 | yes |
| 20 | 1.11 | Mercury+Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Venus+Mars+Mercury+Jupiter+Saturn | 5,638 | 4,586 / 2,673 / 2,309 | 4.3 | 3 | yes |
| 21 | 1.26 | Venus+Earth+Moon+Mars+Jupiter+Saturn | Earth+Moon+Venus+Jupiter+Mars+Saturn | 9,744 | 8,119 / 3,831 / 3,465 | 4.2 | 3 | yes |
| 22 | 1.53 | Venus+Earth+Moon+Mars+Jupiter | Earth+Moon+Venus+Jupiter+Mars | 5,829 | 10,180 / 4,458 / 4,084 | 1.7 | 7 | yes |
| 23 | 1.07 | Mars+Jupiter+Saturn | Jupiter+Saturn+Mars | 15,064 | 25,468 / 8,270 / 7,867 | 0.7 | 2 | yes |
