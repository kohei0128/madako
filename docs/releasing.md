# PyPI release手順

MadakoはGitHub Releaseを起点に、PyPI Trusted Publishing（OIDC）で公開する。長期API tokenやPyPI passwordはGitHub Secretsへ保存しない。

release workflowは次の順に実行する。

1. Releaseのtagをcheckoutし、`pyproject.toml`の`project.version`と`v<version>`が一致することを検証する
2. Python test、Web build、E2E testを実行し、wheelとsdistを一度だけbuildする
3. buildした配布物をworkflow artifactとして保存する
4. 同じartifactをTestPyPIへ公開し、TestPyPIから取得したwheelがartifactとbyte単位で一致することを確認する
5. 隔離環境でCLI、public Python API、Web appをsmoke testする
6. GitHub Environment `pypi`の承認後、同じartifactをPyPIへ公開する

## 初回だけ必要な設定

設定はPyPI、TestPyPI、GitHubの各Web画面で行う。PyPIとTestPyPIは別サービスなので、両方に同じTrusted Publisher設定が必要になる。

### GitHub Environments

リポジトリの **Settings > Environments** で次のEnvironmentを作成する。

- `testpypi`: TestPyPI用。通常は承認者を設定しない
- `pypi`: production PyPI用。**Required reviewers**にrelease承認者を1人以上設定する

productionへのpublishは`pypi` Environmentの承認があるまで開始されない。Environment secretやrepository secretにPyPI tokenを登録しない。

### Trusted Publishers

[TestPyPI](https://test.pypi.org/manage/account/publishing/)と[PyPI](https://pypi.org/manage/account/publishing/)のPending Publisherに、それぞれ次を登録する。

| 項目 | 値 |
| --- | --- |
| PyPI project name | `madako` |
| Owner | `kohei0128` |
| Repository name | `madako` |
| Workflow name | `release.yml` |
| Environment name | TestPyPIでは`testpypi`、PyPIでは`pypi` |

初回publish前には、`https://pypi.org/project/madako/`と`https://test.pypi.org/project/madako/`を確認し、distribution名が引き続き利用可能であることを確認する。Pending Publisherは名前を予約しない。詳しい設定方法は[PyPIのTrusted Publishersドキュメント](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)を参照する。

## Releaseを作成する

### 1. Release準備のPull Requestを作成する

この段階ではversionを更新して検証し、Pull Requestを作成する。tagとGitHub Releaseは
まだ作成しない。

1. PyPIとTestPyPIの両方で未使用のversionを決める。
2. 最新の`main`からversion更新専用branchを作成する。
3. `pyproject.toml`の`project.version`を更新し、`uv lock`で`uv.lock`を同期する。
4. `web/package.json`と`web/package-lock.json`も同じversionへ更新する。`web/`で
   `npm version <version> --no-git-tag-version`を実行すると両方を同期できる。
5. tagとの一致、test、Web build、E2E、配布物をローカルで検証する。
6. 変更をcommit・pushし、`main`向けのPull Requestを作成する。

実行例（`0.1.1`は公開するversionへ置き換える）:

```bash
release_version="0.1.1"
release_branch="feat/bump-version-${release_version//./-}"

git switch main
git pull --ff-only origin main
git switch -c "$release_branch"

# pyproject.tomlのproject.versionを先に更新する
uv lock
(cd web && npm version "$release_version" --no-git-tag-version)
python scripts/verify_release_version.py "v${release_version}"
uv run pytest
npm --prefix web run build
npm --prefix web run test:e2e
release_dist_dir="$(mktemp -d)"
uv build --out-dir "$release_dist_dir"
python scripts/verify_package_contents.py "$release_dist_dir"

git add pyproject.toml uv.lock web/package.json web/package-lock.json
git commit -m "chore: bump version to ${release_version}"
git push -u origin "$release_branch"
gh pr create \
  --base main \
  --head "$release_branch" \
  --title "chore: bump version to ${release_version}" \
  --body "Prepare the Madako ${release_version} release."
```

Pull Requestの説明には各検証結果と、認証や費用などの理由で実行できなかったtestを
記載する。通常CIが成功してから`main`へmergeする。

### 2. Pull Requestのmerge後にReleaseを公開する

この段階で初めてtagを作成する。merge後の`main`をpullし、release対象commitに正しい
versionが含まれることを再確認する。

1. version更新のPull Requestと通常CIが完了していることを確認する。
2. `main`をpullし、tagとpackage versionの一致を検証する。
3. 最新の`main` commitへannotated tagを作成してpushする。
4. そのtagからGitHub Releaseを作成し、公開する。Draftの保存だけではworkflowは
   動かない。
5. TestPyPI publishとsmoke testの完了後、`pypi` Environmentのdeploymentを承認する。

実行例:

```bash
release_version="0.1.1"
git switch main
git pull --ff-only origin main
python scripts/verify_release_version.py "v${release_version}"
git status --short
git tag -a "v${release_version}" -m "Release v${release_version}"
git push origin "v${release_version}"
gh release create "v${release_version}" --verify-tag --generate-notes
```

`git status --short`に出力がある場合は、未commitの変更をreleaseへ混ぜず、原因を確認して
からtagを作成する。

tagはversion変更のmerge前に作成しない。release workflowはtagのcommitをcheckoutする
ため、tag作成後に`main`のversionを直しても、そのworkflowには反映されない。

`release.yml`はGitHub Releaseの`published` eventだけをtriggerにする。branch push、
Pull Request、Draft Releaseからpublish jobは動作しない。tagが`pyproject.toml`の
versionと一致しない場合は、配布物のbuildやuploadより前にworkflowが失敗する。

TestPyPI smoke testが終わると、`publish-pypi` jobが`pypi` Environmentの承認待ちに
なる。ログとTestPyPIのproject pageを確認し、問題がなければ承認する。

## 失敗時の再実行

PyPIとTestPyPIでは、公開済みの同一versionや同一filenameを削除しても上書きできない。

### Version検証で失敗した場合

`Verify release tag and package version`で失敗した場合は、workflowがTestPyPIやPyPIへ
何もuploadしていないことをjob一覧で確認する。既存workflowの再実行は同じtagの
commitを再びcheckoutするため、mainだけを修正しても解決しない。

まだどちらのindexにもuploadしていなければ、version変更をmainへmergeした後、失敗した
GitHub Releaseと誤ったcommitを指すtagを削除し、同じversionで作り直せる。

```bash
release_version="0.1.1"
gh release delete "v${release_version}" --yes
git tag -d "v${release_version}"
git push origin --delete "v${release_version}"

git switch main
git pull --ff-only origin main
python scripts/verify_release_version.py "v${release_version}"
git tag -a "v${release_version}" -m "Release v${release_version}"
git push origin "v${release_version}"
gh release create "v${release_version}" --verify-tag --generate-notes
```

削除前に、失敗箇所がversion検証であり、TestPyPI / PyPI publish jobがskipされたことを
必ず確認する。どちらかへupload済みならtagを作り直さず、新しいversionを採番する。

### Publish開始後に失敗した場合

- TestPyPIへの公開後にsmoke testまたはproduction publishが失敗した場合は、Actions画面から **Re-run failed jobs** を選ぶ。成功済みのTestPyPI publishを含むworkflow全体を再実行しない
- `publish-pypi`が承認待ちで停止した場合は、内容を確認してEnvironment deploymentを承認または拒否する
- 配布物そのものに問題がある場合や、TestPyPI/PyPIへのuploadが一部でも完了した場合は、コードを修正してversionを上げ、新しいtagとGitHub Releaseを作る
- workflow artifactの保存期間は7日。期限切れ後は古いartifactを再buildして同一versionへ公開せず、versionを上げて新しいreleaseとしてやり直す

GitHub Actionsのjob再実行で成功済みjobがskipされる場合、最初のrunでbuildした`madako-distributions` artifactが再利用される。そのためproductionへ公開されるファイルはTestPyPIで検証したファイルと同一になる。
