install_data(
  join_paths('hicolor', 'scalable', 'apps', 'net.armatik.Kitsune.svg'),
  install_dir: join_paths(get_option('datadir'), 'icons', 'hicolor', 'scalable', 'apps'),
)

install_data(
  join_paths('hicolor', 'symbolic', 'apps', 'net.armatik.Kitsune-symbolic.svg'),
  install_dir: join_paths(get_option('datadir'), 'icons', 'hicolor', 'symbolic', 'apps'),
)

action_icons = [
  'funnel-symbolic',
  'genres-symbolic',
  'franchises-symbolic',
  'magnet-symbolic',
  'cross-large-symbolic',
  'seek-backward-10-symbolic',
  'seek-forward-10-symbolic',
  'image-missing-symbolic',
  'sidebar-collapse-right-symbolic',
  'plus-circle-symbolic',
  'home-symbolic',
  'angled-arrows-symbolic',
  'update-symbolic',
  'cloud-filled-symbolic',
  'settings-symbolic',
  'system-search-symbolic',
  'view-list-symbolic',
  'view-grid-symbolic',
  'view-list-bullet-symbolic',
  'view-fullscreen-symbolic',
  'open-menu-symbolic',
  'go-previous-symbolic',
  'go-up-symbolic',
  'object-select-symbolic',
  'media-playback-start-symbolic',
  'media-playback-pause-symbolic',
  'media-skip-backward-symbolic',
  'media-skip-forward-symbolic',
  'applications-graphics-symbolic',
  'drive-harddisk-symbolic',
  'user-trash-symbolic',
  'starred-symbolic',
  'non-starred-symbolic',
  'audio-volume-high-symbolic',
  'window-close-symbolic',
]

foreach icon : action_icons
  install_data(
    join_paths('hicolor', 'scalable', 'actions', APPLICATION_ID + '.' + icon + '.svg'),
    install_dir: join_paths(get_option('datadir'), 'icons', 'hicolor', 'scalable', 'actions'),
  )
endforeach
