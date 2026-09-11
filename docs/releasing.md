# PyPI release手順

MadakoはGitHub Releaseを起点に、PyPI Trusted Publishing（OIDC）で公開する。長期API tokenやPyPI passwordはGitHub Secretsへ保存しない。

release workflowは次の順に実行する。

1. Releaseのtagをcheckoutし、`pyproject.toml`の`project.version`と`v<version>`が一致することを検証する
2. Python test、Web build、E2E testを実行し、wheelとsdistを一度だけbuildする
3. buildした配布物をworkflow artifactとして保存する
4. 同じartifactをTestPyPIへ公開し、TestPyPIから取得したwheelがartifactとbyte単位で一致することを確認する
5. 隔離環境でCLI、public Python API、`web` extraをsmoke testする
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

1. `pyproject.toml`の`project.version`を、まだPyPI/TestPyPIで使っていないversionへ更新する。必要なら`web/package.json`の製品versionも同時に更新する。
2. version変更をPull Requestで`main`へmergeし、通常CIが成功したことを確認する。
3. 最新の`main` commitへ`v<version>`形式のannotated tagを作成してpushする。
4. そのtagからGitHub Releaseを作成し、公開する。Draftの保存だけではworkflowは動かない。

例:

```bash
git switch main
git pull --ff-only
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0
gh release create v0.1.0 --verify-tag --generate-notes
```

`release.yml`はGitHub Releaseの`published` eventだけをtriggerにする。branch push、Pull Request、Draft Releaseからpublish jobは動作しない。tagが`pyproject.toml`のversionと一致しない場合は、公開前にworkflowが失敗する。

TestPyPI smoke testが終わると、`publish-pypi` jobが`pypi` Environmentの承認待ちになる。ログとTestPyPIのproject pageを確認し、問題がなければ承認する。

## 失敗時の再実行

PyPIとTestPyPIでは、公開済みの同一versionや同一filenameを削除しても上書きできない。

- TestPyPIへの公開後にsmoke testまたはproduction publishが失敗した場合は、Actions画面から **Re-run failed jobs** を選ぶ。成功済みのTestPyPI publishを含むworkflow全体を再実行しない
- `publish-pypi`が承認待ちで停止した場合は、内容を確認してEnvironment deploymentを承認または拒否する
- 配布物そのものに問題がある場合や、TestPyPI/PyPIへのuploadが一部でも完了した場合は、コードを修正してversionを上げ、新しいtagとGitHub Releaseを作る
- workflow artifactの保存期間は7日。期限切れ後は古いartifactを再buildして同一versionへ公開せず、versionを上げて新しいreleaseとしてやり直す

GitHub Actionsのjob再実行で成功済みjobがskipされる場合、最初のrunでbuildした`madako-distributions` artifactが再利用される。そのためproductionへ公開されるファイルはTestPyPIで検証したファイルと同一になる。
