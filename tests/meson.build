test_dir = meson.current_source_dir()
test_env = ['PYTHONPATH=' + meson.project_source_root() / 'src']

test_files = [
  'test_models.py',
  'test_watch_positions.py',
  'test_tags_store.py',
  'test_navbar.py',
  'test_storage.py',
  'test_search_index.py',
  'test_release_cache.py',
  'test_user_model.py',
  'test_collection_model.py',
  'test_token_store.py',
  'test_session.py',
  'test_sync_manager.py',
  'test_pending_queue.py',
  'test_episode_index.py',
  'test_sync_flow.py',
  'test_api_client.py',
  'test_auto_collections.py',
]

foreach test_file : test_files
  test_name = test_file.replace('.py', '').replace('test_', '')
  test(test_name, py_installation,
    args: ['-m', 'pytest', test_dir / test_file, '-v'],
    env: test_env,
  )
endforeach

xvfb_run = find_program('xvfb-run', required: false)

widget_test_files = [
  'test_episode_logic.py',
  'test_content_grid.py',
  'test_tags_view_widget.py',
  'test_catalog_view_widget.py',
  'test_genres_view_widget.py',
  'test_release_card_widget.py',
  'test_tag_card_widget.py',
  'test_release_view_widget.py',
  'test_player_view_widget.py',
  'test_profile_view_widget.py',
]

foreach test_file : widget_test_files
  test_name = test_file.replace('.py', '').replace('test_', '')
  if xvfb_run.found()
    test(test_name, xvfb_run,
      args: ['-a', py_installation.full_path(), '-m', 'pytest',
             test_dir / test_file, '-v'],
      env: test_env,
      is_parallel: false,
    )
  else
    test(test_name, py_installation,
      args: ['-m', 'pytest', test_dir / test_file, '-v'],
      env: test_env,
    )
  endif
endforeach
